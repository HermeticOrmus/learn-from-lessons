"""Guess which scenario (docs/SCENARIOS.md) an agent is in, from a bit of text and the working directory.
Cheap keyword and file-type heuristics, English and Chinese. No model calls."""
from __future__ import annotations
import os, re

SCENARIOS = [
    "any", "writing-paper", "writing-outward", "figures-visual", "coding", "debugging", "experiments",
    "research-ideation", "planning-discussion", "release-deploy", "reporting", "long-autonomous", "cost-resources",
]

# (scenario, weight, English patterns (word-bounded regex), Chinese substrings)
_RULES: list[tuple[str, float, list[str], list[str]]] = [
    ("writing-paper", 2.0,
     [r"\.tex", r"\.bib", r"latex", r"overleaf", r"rebuttal", r"camera[- ]ready", r"abstract", r"manuscript",
      r"related work", r"introduction", r"caption", r"reviewers?", r"neurips", r"iclr", r"icml", r"chi", r"acl",
      r"arxiv", r"paper", r"prose", r"paragraph", r"section", r"appendix", r"citations?", r"bibtex"],
     ["论文", "摘要", "引言", "审稿", "改写", "润色", "段落", "章节", "投稿", "附录", "引用", "正文"]),
    ("writing-outward", 1.5,
     [r"e-?mail", r"blog", r"post", r"application", r"cover letter", r"statement", r"newsletter", r"readme",
      r"announcement", r"tweet", r"docs?", r"memo"],
     ["邮件", "博客", "申请", "推文", "公告", "文档", "简历", "回信"]),
    ("figures-visual", 2.0,
     [r"figures?", r"fig\d*", r"plots?", r"charts?", r"diagrams?", r"slides?", r"decks?", r"pptx?", r"\.png",
      r"\.svg", r"matplotlib", r"colou?rs?", r"fonts?", r"layout", r"tables?", r"teaser", r"ui", r"css", r"mermaid"],
     ["画图", "作图", "图表", "这张图", "图片", "图里", "表格", "幻灯片", "配色", "颜色", "字体", "排版", "示意"]),
    ("coding", 1.0,
     [r"\.py", r"\.ts", r"\.tsx", r"\.js", r"\.rs", r"\.go", r"code", r"function", r"refactor", r"implement",
      r"class", r"module", r"api route", r"endpoint", r"lint", r"typecheck", r"tests?", r"cli", r"script"],
     ["代码", "实现", "重构", "函数", "脚本", "接口", "写个"]),
    ("debugging", 2.0,
     [r"pytest", r"traceback", r"stack ?trace", r"exception", r"error", r"errno", r"fails?", r"failing",
      r"failed", r"crash(es|ed)?", r"bug", r"broken", r"doesn'?t work", r"segfault", r"\w+Error", r"debug"],
     ["报错", "错误", "调试", "崩溃", "失败", "不工作", "跑不通", "修复", "异常", "坏了"]),
    ("experiments", 1.5,
     [r"experiments?", r"runs?", r"seeds?", r"evaluations?", r"eval", r"benchmark", r"baselines?", r"ablations?",
      r"datasets?", r"results?", r"metrics?", r"sweep", r"\.ipynb", r"statistic(s|al)?", r"samples?"],
     ["实验", "跑一下", "种子", "评估", "数据", "基线", "消融", "样本", "统计"]),
    ("research-ideation", 1.5,
     [r"ideas?", r"framing", r"research questions?", r"hypothes[ie]s", r"novelty", r"contribution", r"method",
      r"motivation", r"positioning"],
     ["想法", "思路", "研究问题", "假设", "创新", "贡献", "动机", "定位", "方法论"]),
    ("planning-discussion", 1.0,
     [r"why", r"how (do|should|would|can)", r"plan", r"should we", r"what if", r"options?", r"trade-?offs?",
      r"discuss", r"propose", r"roadmap", r"strategy"],
     ["为什么", "怎么", "如何", "讨论", "计划", "方案", "规划", "要不要", "是不是", "思考"]),
    ("release-deploy", 2.5,
     [r"git push", r"push", r"pull request", r"prs?", r"merge", r"release", r"deploy(ment|ed|ing)?", r"vercel",
      r"tag", r"publish", r"ship", r"main branch", r"force[- ]push", r"rebase", r"gh pr", r"ci"],
     ["推送", "部署", "发布", "上线", "合并", "提交", "发版"]),
    ("reporting", 1.5,
     [r"report", r"status", r"summary", r"summari[sz]e", r"progress", r"what did you", r"results? (show|table)",
      r"done\?", r"update me"],
     ["汇报", "报告", "进度", "总结", "结果怎么样", "做完了吗", "现在怎么样"]),
    ("long-autonomous", 1.5,
     [r"overnight", r"autonomous(ly)?", r"unattended", r"while i'?m away", r"keep going", r"in the background",
      r"for hours", r"loop"],
     ["通宵", "自主", "无人", "我不在", "一直跑", "继续做", "后台", "睡觉"]),
    ("cost-resources", 2.5,
     [r"cost", r"credits?", r"budget", r"billing", r"spend(ing)?", r"\$\d+", r"dollars?", r"tokens?", r"gpus?",
      r"compute", r"paid", r"pricing", r"quota", r"api key", r"openrouter", r"batch api"],
     ["花费", "额度", "费用", "预算", "多少钱", "钱", "成本", "算力", "贵", "付费"]),
]

_COMPILED = [(sc, w, re.compile(r"(?<![a-z0-9])(?:" + "|".join(en) + r")(?![a-z0-9])", re.I), zh)
             for sc, w, en, zh in _RULES]

# cwd path hints: (scenario, weight, path-segment substrings)
_CWD = [
    ("writing-paper", 1.0, ["paper", "overleaf", "tech-report", "rebuttal", "manuscript", "thesis"]),
    ("figures-visual", 0.5, ["figure", "teaser", "slides", "preview"]),
]
_CWD_EXT = [("writing-paper", 1.0, (".tex", ".bib")), ("coding", 0.5, (".py", ".ts", ".tsx", ".js", ".rs", ".go"))]


def _cwd_scores(cwd: str | None) -> dict[str, float]:
    out: dict[str, float] = {}
    if not cwd:
        return out
    low = cwd.lower()
    for sc, w, subs in _CWD:
        if any(s in low for s in subs):
            out[sc] = out.get(sc, 0.0) + w
    try:
        names = os.listdir(cwd)[:200]
    except OSError:
        names = []
    for sc, w, exts in _CWD_EXT:
        if any(n.endswith(exts) for n in names):
            out[sc] = out.get(sc, 0.0) + w
    return out


def infer_scenario(text: str, cwd: str | None = None, k: int = 3) -> list[tuple[str, float]]:
    """Ranked (scenario, score) pairs, best first, at most k; [] when nothing matches.
    Text matches dominate; cwd only nudges (it cannot pick a scenario on its own unless text is empty)."""
    text = text or ""
    scores: dict[str, float] = {}
    for sc, w, rx, zh in _COMPILED:
        n = len(rx.findall(text)) + sum(text.count(z) for z in zh)
        if n:
            scores[sc] = w * (1 + 0.5 * min(n - 1, 4))  # repeated hits help, with diminishing returns
    for sc, w in _cwd_scores(cwd).items():
        if scores or not text.strip():
            scores[sc] = scores.get(sc, 0.0) + w
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], SCENARIOS.index(kv[0])))
    return [(sc, round(s, 2)) for sc, s in ranked[:k]]


def best(text: str, cwd: str | None = None) -> str | None:
    r = infer_scenario(text, cwd, k=1)
    return r[0][0] if r else None
