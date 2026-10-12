# -*- coding: utf-8 -*-
"""重绘《AI数字化人才平台-数据流图与类图时序图》中的 DFD 为**大尺寸、可无限放大的 SVG**。

设计约束
--------
1. 配色沿用文档内 Mermaid classDef 的既有语义（proc 蓝 / store 灰 / ext 蓝 /
   本地域绿 / 出网域红 / 出网边界琥珀），**不新造颜色语义**。
2. 字号：主标题 38、副标题 18、方框标题 18、方框正文 14、数据流标签 13。
   在 2720 宽画布上 100% 显示即清晰；矢量可任意放大不失真。
3. 数据流一律**正交折线 + 箭头**，禁止斜线穿框；每条流都有名词短语标签。
"""
from __future__ import annotations

import os

FONT = "'Microsoft YaHei','Noto Sans CJK SC','PingFang SC','Segoe UI',sans-serif"

KIND = {
    "proc":  ("#eaf1fd", "#1b5fd9", "#134ba8"),
    "store": ("#f2f4f7", "#98a2ae", "#20262e"),
    "ext":   ("#eef2fb", "#4266b3", "#1c2430"),
    "local": ("#eaf7f0", "#1f9254", "#166b3e"),
    "cloud": ("#fdeeee", "#d64545", "#9c3434"),
    "stop":  ("#fdeeee", "#d64545", "#9c3434"),
    "det":   ("#eef2fb", "#4266b3", "#2f4a80"),
    "amber": ("#fff8e6", "#b7791f", "#8a6216"),
    "note":  ("#fbfcfe", "#c6d0dc", "#5b6b7c"),
}

INK = "#33414f"
MUTED = "#6b7a8a"
FLOW = "#7a8794"
FLOW_D = "#5b6b7c"
GREEN = "#1f9254"
RED = "#d64545"
AMBER = "#b7791f"


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def tw(s: str, size: float) -> float:
    w = 0.0
    for ch in s:
        o = ord(ch)
        if o > 0x2E80:
            w += size * 1.0
        elif ch in "·/|":
            w += size * 0.42
        elif ch in "ilejft.,;:' ":
            w += size * 0.32
        else:
            w += size * 0.56
    return w


class Svg:
    def __init__(self, w: int, h: int, title: str, desc: str):
        self.w, self.h = w, h
        self.parts: list[str] = []
        self.head = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" role="img" font-family="{FONT}">',
            f"<title>{esc(title)}</title>",
            f"<desc>{esc(desc)}</desc>",
            "<defs>",
        ]
        for name, col in (("g", FLOW_D), ("gr", GREEN), ("rd", RED),
                          ("am", AMBER), ("gy", "#aab4c0")):
            self.head.append(
                f'<marker id="ar-{name}" viewBox="0 0 10 10" refX="8.5" refY="5" '
                f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                f'<path d="M2 1L8 5L2 9" fill="none" stroke="{col}" stroke-width="1.6" '
                f'stroke-linecap="round" stroke-linejoin="round"/></marker>'
            )
        self.head.append("</defs>")
        self.parts.append(f'<rect x="0" y="0" width="{w}" height="{h}" fill="#ffffff"/>')

    def rect(self, x, y, w, h, fill, stroke, rx=12, sw=1.4, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')

    def text(self, x, y, s, size=14, color=INK, weight="400", anchor="middle",
             baseline="middle", italic=False):
        st = ' font-style="italic"' if italic else ""
        self.parts.append(
            f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
            f'font-weight="{weight}" text-anchor="{anchor}" '
            f'dominant-baseline="{baseline}"{st}>{esc(s)}</text>')

    def dot(self, cx, cy, r=6, fill=RED):
        self.parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}"/>')

    def chip(self, x, y, s, size=13, fg="#ffffff", bg=RED, pad=9, rx=7, weight="600"):
        w = tw(s, size) + pad * 2
        h = size + 11
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w:.0f}" height="{h:.0f}" rx="{rx}" fill="{bg}"/>')
        self.text(x + w / 2, y + h / 2 + 0.5, s, size, fg, weight)

    def box(self, b):
        x, y, w, h = b["x"], b["y"], b["w"], b["h"]
        fill, stroke, tcol = KIND[b["kind"]]
        self.rect(x, y, w, h, fill, stroke, rx=b.get("rx", 12), sw=b.get("sw", 1.5), dash=b.get("dash"))
        if b.get("accent"):
            self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="6" rx="3" fill="{b["accent"]}"/>')
        cx = x + w / 2
        tsize = b.get("tsize", 18)
        ty = y + b.get("tpad", 36)
        self.text(cx, ty, b["title"], tsize, b.get("tcolor", tcol), "600")
        lines = b.get("lines", [])
        lsize = b.get("lsize", 14)
        ly = ty + tsize * 0.95
        for i, ln in enumerate(lines):
            col, wt = b.get("lcolor", MUTED), "400"
            if ln.startswith("!"):
                ln, col, wt = ln[1:], b.get("hl", RED), "600"
            self.text(cx, ly + i * (lsize + 9), ln, lsize, col, wt)
        for m in b.get("marks", []):
            self.chip(x + w - tw(m[0], 12.5) - 20, y + 8, m[0], 12.5, "#ffffff", m[1])

    def poly(self, pts, color=FLOW, sw=1.7, marker="g", dash=None):
        d = "M" + " L".join(f"{p[0]:.1f} {p[1]:.1f}" for p in pts)
        da = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}" '
            f'stroke-linejoin="round" stroke-linecap="round" marker-end="url(#ar-{marker})"{da}/>')

    def label(self, x, y, s, size=13, color=INK, bg="#ffffff", weight="500",
              anchor="middle", pad=5, opacity=0.95):
        w = tw(s, size)
        if bg:
            x0 = x - w / 2 - pad if anchor == "middle" else (x - pad if anchor == "start" else x - w - pad)
            self.parts.append(
                f'<rect x="{x0:.0f}" y="{y - size / 2 - 4:.0f}" '
                f'width="{w + pad * 2:.0f}" height="{size + 8:.0f}" rx="5" '
                f'fill="{bg}" opacity="{opacity}"/>')
        self.text(x, y, s, size, color, weight, anchor)

    def save(self, path):
        self.parts.append("</svg>")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(self.head + self.parts))
        return path


# =====================================================================
# 图 1 · DF-1 数据流向总图
# =====================================================================
def fig1() -> Svg:
    W, H = 2720, 1460
    s = Svg(W, H, "AI 数字化人才平台 · 数据流向总图（DF-1 一级分解）",
            "外部实体 → 加工 P1~P9 → 数据存储 DS-1~DS-4，并标注三条红线")

    s.text(64, 62, "AI 数字化人才平台 · 数据流向总图", 38, "#0e3b66", "700", "start")
    s.text(64, 104, "DF-1 一级分解 ｜ 外部实体 → 加工 P1~P9 → 数据存储 DS-1~DS-4 ｜ "
                    "唯一出网通道 = P7 → 云端大模型服务", 18, MUTED, "400", "start")

    lx, ly = 1980, 34
    s.rect(lx, ly, 676, 92, "#f8fafc", "#dbe3ec", rx=12, sw=1.2)
    items = [("加工 Process", KIND["proc"]), ("数据存储 Store", KIND["store"]),
             ("外部实体", KIND["ext"]), ("本地域 · 不出网", KIND["local"]),
             ("出网域", KIND["cloud"])]
    for i, (t, c) in enumerate(items):
        cx = lx + 22 + (i % 3) * 226
        cy = ly + 26 + (i // 3) * 40
        s.rect(cx, cy - 10, 26, 20, c[0], c[1], rx=5, sw=1.3)
        s.text(cx + 36, cy, t, 14, INK, "400", "start")

    R1H, R2Y, R2H, R3Y, R3H = 160, 540, 236, 862, 214

    # Row1
    s.box({"kind": "proc", "x": 64, "y": R1H, "w": 236, "h": 162, "title": "P2 组织与用户维护",
           "lines": ["组织 / 用户 / 角色", "权限 / 字典配置", "配置与权限写入 DS-1", "F-1.1 · F-1.7"], "lsize": 13.5})
    s.box({"kind": "local", "x": 520, "y": R1H, "w": 1180, "h": 162, "title": "本地模型服务（不出网）",
           "tsize": 20, "lines": ["qwen2.5:3b · qwen3:8b   ｜   bge-m3 · bge-reranker-v2-m3 · NER",
                                   "!● 承载全部隐私相关加工：字段抽取 / 向量化 / 问题改写 / 脱敏校验",
                                   "!隐私加工区，不是可选优化"], "hl": GREEN, "lcolor": "#4f7a63"})
    s.box({"kind": "cloud", "x": 1780, "y": R1H, "w": 560, "h": 162, "title": "云端大模型服务",
           "tsize": 20, "lines": ["cloud-llm-a / cloud-llm-b", "!● 唯一出网目的地"], "accent": RED})

    # Row2
    row2 = [
        ("U", "ext", 64, 240, "用户（按角色）", ["ADM 管理员 · HR 招聘 · MGR 经理", "EMP 员工 · AIOPS 运维 · AUDIT 审计"]),
        ("P1", "proc", 344, 214, "P1 认证与权限判定", ["F-1.2 / F-1.3", "API-01~06"]),
        ("P3", "proc", 598, 214, "P3 简历导入与解析", ["F-3.1 / 3.2 / 3.8", "API-36~43"]),
        ("P4", "proc", 852, 214, "P4 字段复核与字典归一", ["F-3.2 / 3.3", "API-44~49"]),
        ("P5", "proc", 1106, 214, "P5 画像与标签生成", ["F-3.4 / 3.4.1", "API-50~56"]),
        ("P6", "proc", 1360, 214, "P6 查重与合并", ["F-3.5", "API-57~61"]),
        ("P7", "proc", 1614, 250, "P7 语义检索与档案问答", ["F-3.6 / 3.7 / 3.9", "API-62 / 63", "!★ 唯一出网加工"]),
        ("P8", "proc", 1904, 214, "P8 变更留痕与个人权利", ["F-3.10 / 3.11", "API-67~80"]),
        ("P9", "proc", 2158, 214, "P9 审计与 AI 调用日志", ["F-1.5 / F-9.5", "!只追加 · 按月分区"]),
    ]
    for cid, kind, x, w, title, lines in row2:
        s.box({"kind": kind, "x": x, "y": R2Y, "w": w, "h": R2H, "title": title, "lines": lines, "lsize": 14})

    # Row3
    for x, w, title, lines in [
        (64, 240, "DS-3 Redis", ["sess / cache / rate", "agent_mem / queue", "不存 Agent 权威状态"]),
        (344, 240, "DS-4 MinIO", ["talent-files", "原始简历文件", "解析结果落 DS-1"]),
        (852, 560, "DS-1 MySQL", ["talent_platform · TBL-01 ~ TBL-28",
                                  "用户 / 权限 / 字典 / 档案 / 标签 / 导入 / Agent 状态 / 三类日志",
                                  "不存向量、大文本、原始文件"]),
        (1614, 480, "DS-2 Milvus", ["talent_vec · COL-01~03 脱敏检索单元",
                                    "!★ 明文身份（姓名 / 手机 / 身份证 / 邮箱）一律不入库"]),
    ]:
        s.box({"kind": "store", "x": x, "y": R3Y, "w": w, "h": R3H, "title": title, "lines": lines, "lsize": 14})

    # ---- 数据流 ----
    # 用户 ⇄ P1
    s.poly([(304, 636), (344, 636)], FLOW_D, 2.0)
    s.poly([(344, 700), (304, 700)], FLOW_D, 2.0)
    s.poly([(324, 620), (324, 500)], "#c9d2dc", 1.0, "gy")
    s.label(324, 488, "身份凭据 ⇄ Token + 数据域", 13.5, FLOW_D)

    # 用户输入总线（5 条，走廊 y=390~470）
    fan = [(390, 705, "简历文件（≤100 份 · 单件 ≤50MB）"),
           (410, 959, "复核结论（采纳 / 修正 / 免复核）"),
           (430, 1467, "合并 / 忽略 / 回滚指令"),
           (450, 1739, "自然语言检索 / 问答问题"),
           (470, 2011, "查阅 / 更正 / 删除 / 导出申请")]
    for cy, tx, lab in fan:
        s.poly([(184, R2Y), (184, cy), (tx, cy), (tx, R2Y)], FLOW, 1.5, "g", dash="7 5")
        s.label(tx - 158, cy - 13, lab, 12.5, FLOW_D)

    # P2 → P1 / 审计总线 / P1 → P7
    s.poly([(150, R1H + 162), (150, 340), (451, 340), (451, R2Y)], FLOW_D, 1.7)
    s.label(300, 328, "权限点与角色绑定", 12.5, FLOW_D)
    s.poly([(451, R2Y), (451, 364), (1739, 364), (1739, R2Y)], AMBER, 1.7, "am", dash="7 5")
    s.label(1090, 352, "当前用户上下文（P1 同时门控 P7 的检索数据域）", 12.5, "#8a6216")
    s.poly([(959, R2Y), (959, 496), (2265, 496), (2265, R2Y)], AMBER, 1.9, "am", dash="7 5")
    s.label(1610, 484, "各加工操作事件（配置 / 复核 / 审批 / 导出）", 12.5, "#8a6216")
    # P2 → DS-1
    s.poly([(276, R1H + 162), (276, 514), (830, 514), (830, 806), (920, 806), (920, R3Y)], FLOW_D, 1.6, dash="7 5")
    s.label(556, 502, "配置变更 + 留痕", 12.5, FLOW_D)

    # 本地模型 ⇄ 加工
    for x in (700, 1180, 1640):
        s.poly([(x, R2Y), (x, R1H + 162)], GREEN, 1.8, "gr")
    for x in (760, 1270, 1690):
        s.poly([(x, R1H + 162), (x, R2Y)], GREEN, 1.8, "gr")
    s.label(712, 430, "简历原文（明文 · 不出网）", 12.5, "#166b3e", anchor="start")
    s.label(772, 470, "字段值 + 逐字段置信度", 12.5, "#166b3e", anchor="start")
    s.label(1192, 430, "向量化请求", 12.5, "#166b3e", anchor="start")
    s.label(1282, 470, "向量（1024 维）", 12.5, "#166b3e", anchor="start")
    s.label(1652, 430, "问题改写 / 精排请求", 12.5, "#166b3e", anchor="start")
    s.label(1702, 470, "改写后问题 / 精排分", 12.5, "#166b3e", anchor="start")

    # 出网（唯一）
    s.poly([(1800, R2Y), (1800, R1H + 162)], RED, 2.8, "rd")
    s.poly([(2000, R1H + 162), (2000, R2Y)], RED, 2.8, "rd")
    s.label(1814, 400, "脱敏占位符片段 + Prompt", 14, "#9c3434", weight="700", anchor="start")
    s.label(1814, 424, "← 唯一出网数据流", 14, "#9c3434", weight="700", anchor="start")
    s.label(2014, 470, "生成文本（仍含占位符）", 12.5, "#9c3434", anchor="start")

    # 加工主线
    for a, b, lab in ((812, 852, "待复核字段"), (1066, 1106, "已确认字段值"), (1320, 1360, "档案就绪信号")):
        s.poly([(a, 658), (b, 658)], FLOW_D, 1.9)
        s.label((a + b) / 2, 638, lab, 11.5, FLOW_D)
    s.poly([(1864, 658), (2158, 658)], FLOW_D, 1.8)
    s.label(2011, 638, "AI 调用记录 / 检索审计", 12, FLOW_D)

    # 变更留痕总线
    s.poly([(1000, R2Y + R2H), (1000, 786), (1980, 786), (1980, R2Y + R2H)], AMBER, 1.7, "am", dash="7 5")
    s.label(1490, 774, "字段级 / 合并变更留痕（只追加）", 12.5, "#8a6216")

    # 写存储
    def jog(sx, lane, dx, lab, tx=None, col=FLOW, marker="g", dash=None):
        tx = dx if tx is None else tx
        s.poly([(sx, R2Y + R2H), (sx, lane), (dx, lane), (dx, R3Y)], col, 1.7, marker, dash=dash)
        s.label((sx + dx) / 2, lane - 13, lab, 12.5, FLOW_D)
    jog(650, 796, 464, "原始文件字节流")
    jog(705, 806, 900, "文件清单 + 待复核字段")
    jog(1213, 826, 2000, "脱敏检索单元（写入前必脱敏）", col=RED, marker="rd")
    jog(1467, 836, 1390, "重复候选对 + 相似度")
    jog(451, 846, 184, "会话 / 限流计数")
    jog(2265, 856, 1300, "审计日志 + AI 调用日志（只追加）", col=AMBER, marker="am")
    s.poly([(959, R2Y + R2H), (959, R3Y)], FLOW, 1.7)
    s.label(1050, 808, "字典归一后的字段值", 12.5, FLOW_D, anchor="start")
    s.poly([(1190, R2Y + R2H), (1190, R3Y)], FLOW, 1.7)
    s.label(1206, 830, "受控标签关系 TBL-19", 12.5, FLOW_D, anchor="start")
    s.poly([(1739, R2Y + R2H), (1739, R3Y)], FLOW, 1.7)
    s.label(1755, 812, "权限前置过滤表达式", 12.5, FLOW_D, anchor="start")
    s.poly([(1806, R3Y), (1806, R2Y + R2H)], FLOW, 1.8)
    s.label(1822, 842, "脱敏检索片段 Top-N", 12.5, FLOW_D, anchor="start")
    s.poly([(2060, R2Y + R2H), (2060, R3Y)], FLOW, 1.7)
    s.label(2076, 812, "向量同步清理指令", 12.5, FLOW_D, anchor="start")
    s.poly([(1960, R2Y + R2H), (1960, 846), (1420, 846), (1420, R3Y)], FLOW, 1.7)
    s.label(1690, 834, "申请单 / 留痕 / 软删指令", 12.5, FLOW_D)

    # 红线面板
    PY = 1120
    s.rect(64, PY, 2600, 300, "#fffdf7", "#e8d9b0", rx=14, sw=1.4)
    s.dot(104, PY + 40, 8)
    s.text(124, PY + 40, "DF-1 上最容易被忽略的三条红线", 20, "#8a6216", "700", "start")
    for i, (n, t, d) in enumerate([
        ("①", "P5 是唯一向 DS-2 写入的加工，且写入前必须脱敏",
         "向量库里的数据无法事后清洗 —— 一旦灌入明文，只能全量重建。"),
        ("②", "P7 是唯一通往 CLOUD 的加工",
         "若将来出现第二个通往云端的加工，即为架构违规（数据泄露）。"),
        ("③", "P9 只被写入、不被 P1~P8 读回（除日志查询）",
         "三类日志一律只追加；任何「读日志改业务」都是设计错误。"),
    ]):
        y = PY + 88 + i * 66
        s.text(104, y + 14, n, 22, RED, "700", "start")
        s.text(140, y, t, 17.5, "#5b4a1f", "600", "start")
        s.text(140, y + 28, d, 14.5, "#8a7a55", "400", "start")

    # 右侧：存储写入一览
    tx0 = 1760
    s.text(tx0, PY + 40, "数据存储写入一览（本图为主干流，全量见 DF-2 / DF-3）", 16, "#8a6216", "600", "start")
    rows = [("DS-1 MySQL", "P1/P2/P3/P4/P5/P6/P7/P8/P9"),
            ("DS-2 Milvus", "P5（脱敏检索单元）· P6/P8（删除清理）"),
            ("DS-3 Redis", "P1（会话/限流）· P3（队列/进度）"),
            ("DS-4 MinIO", "P3（原始简历文件）")]
    for i, (a, b) in enumerate(rows):
        y = PY + 78 + i * 40
        s.text(tx0, y, a, 14.5, "#5b4a1f", "600", "start")
        s.text(tx0 + 180, y, b, 14, "#8a7a55", "400", "start")

    return s


# =====================================================================
# 图 2 · DF-3 检索与问答链路（Level 2，P7 分解）+ 出网边界
# =====================================================================
def fig2() -> Svg:
    W, H = 1880, 1980
    s = Svg(W, H, "AI 数字化人才平台 · 检索与问答链路（DF-3）与出网边界",
            "P7.1~P7.9 逐级加工；唯一出网通道 = P7.7 → 云端，出网前经 P7.6 二次 NER 校验")

    s.text(64, 62, "检索与问答链路 · DF-3（P7 分解）", 36, "#0e3b66", "700", "start")
    s.text(64, 102, "①→⑨ 为主干数据流；绿色 = 本地模型（不出网）；红色 = 出网 / 拒答出口；"
                    "琥珀 = 确定性判定", 17, MUTED, "400", "start")

    CX, CW = 660, 480
    chain = [
        ("P7.1 会话加载与问题改写", ["加载会话上下文（DS-3）", "指代消解 / 问题改写（本地模型）"]),
        ("P7.2 权限裁剪（确定性）", ["数据域 tenant_id / org_id / data_scope", "全部越权 → 40301；部分越权 → clipped=true"]),
        ("P7.3 混合召回（向量 + 关键词）", ["向量召回 Top-20（expr 前置过滤）", "BM25 关键词召回 Top-20（同权限条件）"]),
        ("P7.4 RRF 融合 + Rerank 精排", ["RRF 融合两路候选", "Rerank 精排（本地模型）"]),
        ("P7.5 阈值判定（确定性）", ["精排分 < 0.35 → 不调用大模型", "tokens_in = 0（可直接验证未调用）"]),
        ("P7.6 出网前二次 NER 校验", ["发现残留明文身份 → 拦截并记日志", "绝不降级为明文出网"]),
        ("P7.7 Prompt 组装（强制逐条引用）", ["组装占位符片段 + Prompt", "唯一出网数据流的产生点"]),
        ("P7.8 占位符本地回填", ["在服务端完成，不经任何外部服务", "回填失败 → 拒答，不降级为明文"]),
        ("P7.9 引用溯源后置校验", ["引用可定位 talent_id + field_code + chunk_pk", "输出回答 + 引用卡片（SSE 流式）"]),
    ]
    ys = [150 + i * 190 for i in range(9)]
    for (title, lines), y in zip(chain, ys):
        s.box({"kind": "proc", "x": CX, "y": y, "w": CW, "h": 140, "title": title, "lines": lines, "lsize": 13.5})

    # 左：用户
    s.box({"kind": "ext", "x": 70, "y": 120, "w": 400, "h": 156, "title": "用户（ADM / HR / MGR / EMP）",
           "lines": ["自然语言问题 + 提问范围", "接收：回答+引用卡片 / 40301 / refusal"]})

    # 右：模型 / 存储 / 出口
    s.box({"kind": "store", "x": 1280, "y": 150, "w": 460, "h": 110, "title": "DS-3 Redis",
           "lines": ["sess:* 会话上下文"], "lsize": 13.5})
    s.box({"kind": "local", "x": 1280, "y": 320, "w": 460, "h": 160, "title": "本地模型服务（不出网）",
           "lines": ["qwen2.5:3b · bge-m3 · reranker", "改写 / 向量化 / 精排全在本地", "!隐私加工区"], "lsize": 13.5, "hl": GREEN})
    s.box({"kind": "store", "x": 1280, "y": 560, "w": 460, "h": 160, "title": "DS-2 Milvus",
           "lines": ["COL-01~03 仅脱敏检索单元", "权限前置过滤 expr：tenant_id + org_id"], "lsize": 13.5})
    s.box({"kind": "stop", "x": 1280, "y": 920, "w": 460, "h": 150, "title": "拒答 / 兜底出口",
           "lines": ["LOW_SIMILARITY（低于阈值）", "NER_RESIDUAL（残留明文）", "MASK_REQUIRED（回填失败）"], "lsize": 13, "accent": RED})
    s.box({"kind": "cloud", "x": 1280, "y": 1150, "w": 460, "h": 150, "title": "云端大模型服务",
           "lines": ["cloud-llm-a / cloud-llm-b", "!● 唯一出网目的地"], "lsize": 13.5, "accent": RED})
    s.box({"kind": "store", "x": 1280, "y": 1420, "w": 460, "h": 170, "title": "DS-1 MySQL",
           "lines": ["TBL-11 档案 · TBL-19 标签 · TBL-24 AI 调用日志", "场景 / 模型 / Prompt 版本 / token", "耗时 / 成本 / mask_applied"], "lsize": 13.5})

    def vchain(i, j, lab, num=None):
        y0 = ys[i] + 140
        y1 = ys[j]
        s.poly([(CX + CW / 2, y0), (CX + CW / 2, y1)], FLOW_D, 2.0)
        if lab:
            s.label(CX + CW / 2 + 14, (y0 + y1) / 2, lab, 12.5, FLOW_D, anchor="start")
        if num:
            s.text(CX - 18, (y0 + y1) / 2, num, 17, RED, "700", "end")

    # 主干
    vchain(0, 1, "② 数据域（tenant_id / org_id / data_scope）", "②")
    vchain(1, 2, "部分越权：裁剪后的提问范围 + clipped=true")
    vchain(2, 3, "两路候选（各 Top-20）")
    vchain(3, 4, "③ Top-5 片段 + 精排分", "③")
    vchain(4, 5, "④ 高于阈值：进入脱敏二次校验", "④")
    vchain(5, 6, "⑤ 校验通过的占位符片段", "⑤")
    vchain(6, 7, "（组装完成后出网 → ⑦ 回填）")
    vchain(7, 8, "⑧ 已回填真值的文本", "⑧")

    # ① 用户 → P7.1
    s.poly([(470, 198), (CX, 198)], FLOW_D, 2.0)
    s.label(560, 182, "① 自然语言问题 + 提问范围", 13, FLOW_D)
    # P7.2 → 用户（40301）
    s.poly([(CX, 420), (620, 420), (620, 292), (300, 292)], RED, 1.8, "rd")
    s.label(430, 306, "全部越权：直接 40301，不进流", 12.5, "#9c3434")
    # ⑨ P7.9 → 用户
    s.poly([(CX, 1740), (40, 1740), (40, 178), (70, 178)], FLOW_D, 2.0)
    s.label(360, 1724, "⑨ 回答 + 引用卡片（SSE 流式）", 13.5, FLOW_D)

    # 右侧连线
    s.poly([(CX + CW, 205), (1230, 205), (1230, 205), (1280, 205)], FLOW, 1.7)
    s.label(1255, 188, "会话上下文", 12, FLOW_D)
    s.poly([(CX + CW, 240), (1200, 240), (1200, 400), (1280, 400)], GREEN, 1.7, "gr")
    s.label(1190, 300, "指代消解 / 改写请求", 12, "#166b3e", anchor="end")
    s.poly([(1280, 440), (1215, 440), (1215, 262), (CX + CW, 262)], GREEN, 1.7, "gr")
    s.label(1350, 460, "改写后的问题", 12, "#166b3e")
    s.poly([(CX + CW, 440), (1245, 440), (1245, 610), (1280, 610)], RED, 1.9, "rd")
    s.label(1235, 700, "🔴 权限前置过滤表达式", 12.5, "#9c3434", anchor="end")
    s.poly([(CX + CW, 580), (1280, 580)], FLOW, 1.7)
    s.label(1180, 564, "向量召回 Top-20", 12, FLOW_D, anchor="end")
    s.poly([(1280, 660), (CX + CW, 660)], FLOW, 1.7)
    s.label(1400, 680, "Top-20 脱敏片段", 12, FLOW_D)
    s.poly([(CX + CW, 645), (1250, 645), (1250, 1500), (1280, 1500)], FLOW, 1.6, dash="7 5")
    s.label(1265, 1000, "BM25 关键词召回 Top-20", 12, FLOW_D, anchor="end")
    s.poly([(CX + CW, 780), (1230, 780), (1230, 470), (1280, 470)], GREEN, 1.7, "gr")
    s.label(1340, 500, "候选对 (query, 片段)", 12, "#166b3e")
    s.poly([(1280, 380), (1210, 380), (1210, 810), (CX + CW, 810)], GREEN, 1.7, "gr")
    s.label(1200, 880, "精排分", 12, "#166b3e", anchor="end")
    s.poly([(CX + CW, 970), (1280, 970)], RED, 1.9, "rd")
    s.label(1270, 952, "精排分 < 0.35：不调用大模型", 12, "#9c3434", anchor="end")
    s.poly([(CX + CW, 1150), (1255, 1150), (1255, 1010), (1280, 1010)], RED, 1.9, "rd")
    s.label(1245, 1090, "残留明文：拦截", 12, "#9c3434", anchor="end")
    s.poly([(CX + CW, 1330), (1265, 1330), (1265, 1225), (1280, 1225)], RED, 2.8, "rd")
    s.label(1250, 1420, "⑥ 占位符片段 + Prompt", 13.5, "#9c3434", weight="700", anchor="end")
    s.label(1250, 1444, "（唯一出网数据流）", 13.5, "#9c3434", weight="700", anchor="end")
    s.poly([(1280, 1255), (1225, 1255), (1225, 1550), (CX + CW, 1550)], RED, 2.4, "rd")
    s.label(1400, 1570, "⑦ 生成文本（仍含占位符）+ token 用量", 12.5, "#9c3434")
    s.poly([(CX + CW, 1730), (1255, 1730), (1255, 1560), (1280, 1560)], FLOW, 1.6, dash="7 5")
    s.label(1245, 1680, "引用校验 / 调用留痕", 12, FLOW_D, anchor="end")

    # 红线说明
    s.rect(70, 1810, 1670, 140, "#fffdf7", "#e8d9b0", rx=14, sw=1.4)
    s.dot(106, 1846, 8)
    s.text(126, 1846, "本图的五条红线", 19, "#8a6216", "700", "start")
    s.text(126, 1884, "① 权限前置：过滤表达式在检索前下发，不允许「先召回全量再应用层过滤」　"
                       "② 阈值判定确定性：精排分与阈值比对，不交大模型判断", 14.5, "#8a7a55", "400", "start")
    s.text(126, 1914, "③ 出网前二次 NER 校验　④ 本地回填不经任何外部服务，失败即拒答　"
                       "⑤ 无依据不编造：低于阈值直接兜底，tokens_in = 0 可验证未调用模型", 14.5, "#8a7a55", "400", "start")
    return s


# =====================================================================
# 图 3 · DF-2 档案导入与解析链路（Level 2，P3 分解）
# =====================================================================
def fig3() -> Svg:
    W, H = 1880, 2050
    s = Svg(W, H, "AI 数字化人才平台 · 档案导入与解析链路（DF-2）",
            "P3.1~P3.8 逐级加工；P3.6 脱敏必须在 P3.7 向量化之前")

    s.text(64, 62, "档案导入与解析链路 · DF-2（P3 分解）", 36, "#0e3b66", "700", "start")
    s.text(64, 102, "绿色 = 本地模型（简历原文不出网）；红色 = 脱敏与向量库写入（写入前必脱敏）；"
                    "灰色虚线 = 失败 / 兜底分支", 17, MUTED, "400", "start")

    CX, CW = 660, 480
    chain = [
        ("P3.1 幂等校验与任务登记", ["Idempotency-Key 命中 → 返回首次任务（40903）", "任务记录 status=0 待处理 · 原始文件入 DS-4"]),
        ("P3.2 格式识别与文本提取", ["压缩包解包后总份数仍受 ≤100 限制", "加密 / 格式不支持 → parse_status=2 记原因"]),
        ("P3.3 字段抽取（本地模型，不出网）", ["简历原文（明文）→ 本地模型", "逐字段 raw_value / confidence 写入 DS-1"]),
        ("P3.4 字典归一与受控词表取值", ["字典值归一（education_level 等）", "字典外新词：提交 AI 建议，不得直接入库"]),
        ("P3.5 置信度阈值判定（确定性）", ["高影响字段且低于阈值 → 入复核队列", "高影响且达标 → 直接入库分支"]),
        ("P3.6 脱敏（生成检索单元）", ["姓名 / 手机 / 身份证 / 邮箱 → 占位符", "★ 必须在向量化之前完成"]),
        ("P3.7 向量化与索引写入", ["脱敏文本 → 本地模型 → 1024 维向量", "写入 COL-01/02/03（带 tenant_id + org_id 标量）"]),
        ("P3.8 档案入库与留痕", ["TBL-11 talent_base + TBL-12~15 履历明细", "TBL-22 留痕 change_source=0（AI 解析）"]),
    ]
    ys = [180 + i * 190 for i in range(8)]
    for (title, lines), y in zip(chain, ys):
        s.box({"kind": "proc", "x": CX, "y": y, "w": CW, "h": 140, "title": title, "lines": lines, "lsize": 13.5})
    y5 = ys[-1] + 190
    s.box({"kind": "proc", "x": CX, "y": y5, "w": CW, "h": 128, "title": "P5 画像与标签生成",
           "lines": ["接收 P3.8 的档案就绪信号（下游）"], "lsize": 13.5, "rx": 12})

    # 左：用户、复核
    s.box({"kind": "ext", "x": 70, "y": 150, "w": 400, "h": 156, "title": "HR / ADM",
           "lines": ["简历文件（单批 ≤100 份）", "Idempotency-Key（不可逆动作幂等）"]})
    s.box({"kind": "proc", "x": 70, "y": 1000, "w": 400, "h": 156, "title": "P4 字段复核与字典归一",
           "lines": ["接收 P3.5 的复核队列", "人工确认后回写归一字段"]})

    # 右：模型 / 存储
    s.box({"kind": "local", "x": 1280, "y": 400, "w": 460, "h": 160, "title": "本地模型服务（不出网）",
           "lines": ["qwen2.5:3b 字段抽取", "bge-m3 向量化（1024 维）", "!简历原文永不出网"], "lsize": 13.5, "hl": GREEN})
    s.box({"kind": "store", "x": 1280, "y": 640, "w": 460, "h": 150, "title": "DS-4 MinIO",
           "lines": ["talent-files", "原始简历文件（PDF / Word / 图片）"], "lsize": 13.5})
    s.box({"kind": "store", "x": 1280, "y": 860, "w": 460, "h": 130, "title": "DS-3 Redis",
           "lines": ["queue:* 导入任务队列", "agent_mem:* 短期记忆"], "lsize": 13.5})
    s.box({"kind": "store", "x": 1280, "y": 1050, "w": 460, "h": 170, "title": "DS-1 MySQL",
           "lines": ["TBL-20/21 任务与进度 · TBL-16 解析结果", "TBL-22 变更留痕 · TBL-25/26 Agent 状态", "TBL-11 档案 · TBL-12~15 履历明细"], "lsize": 13, "accent": "#1b5fd9"})
    s.box({"kind": "store", "x": 1280, "y": 1290, "w": 460, "h": 170, "title": "DS-2 Milvus",
           "lines": ["COL-01 resume_vec · COL-02 skill_vec", "COL-03 project_vec", "!★ 仅脱敏检索单元，明文身份不入库"], "lsize": 12.5, "hl": RED, "accent": RED})

    def vchain(i, j, lab, red=False):
        y0 = ys[i] + 140
        y1 = ys[j]
        s.poly([(CX + CW / 2, y0), (CX + CW / 2, y1)], RED if red else FLOW_D, 2.0 if not red else 2.6, "rd" if red else "g")
        if lab:
            s.label(CX + CW / 2 + 14, (y0 + y1) / 2, lab, 12.5, "#9c3434" if red else FLOW_D, anchor="start")

    vchain(0, 1, "待处理任务（入队）")
    vchain(1, 2, "文本类直接提取 / 扫描件转 OCR 请求")
    vchain(2, 3, "字段值 + 逐字段置信度")
    vchain(3, 4, "字典归一后的字段值")
    vchain(4, 5, "达标字段 / 复核后字段")
    vchain(5, 6, "脱敏文本（不含明文身份）", red=True)
    vchain(6, 7, "向量写入完成")
    s.poly([(CX + CW / 2, ys[-1] + 140), (CX + CW / 2, y5)], FLOW_D, 2.0)
    s.label(CX + CW / 2 + 14, (ys[-1] + 140 + y5) / 2, "档案就绪信号", 12.5, FLOW_D, anchor="start")

    # ① 用户 → P3.1
    s.poly([(470, 228), (CX, 228)], FLOW_D, 2.0)
    s.label(560, 212, "① 简历文件 + Idempotency-Key", 13, FLOW_D)
    s.poly([(CX, 250), (560, 250), (560, 306), (300, 306)], FLOW_D, 1.6, dash="7 5")
    s.label(420, 292, "幂等键命中：返回首次任务（40903）", 12.5, FLOW_D)
    # P3.8 → 用户（SSE）
    s.poly([(CX, ys[-1] + 70), (70, ys[-1] + 70)], FLOW_D, 1.8)
    s.label(360, ys[-1] + 54, "SSE 进度事件 progress / done + 任务汇总", 12.5, FLOW_D)
    # P3.5 ⇄ P4
    s.poly([(CX, 1100), (540, 1100), (540, 1078), (470, 1078)], AMBER, 1.8, "am")
    s.label(560, 1122, "入复核队列", 12.5, "#8a6216", anchor="start")
    s.poly([(470, 1030), (600, 1030), (600, 1040), (CX, 1040)], AMBER, 1.8, "am")
    s.label(610, 1012, "人工确认结果", 12.5, "#8a6216", anchor="start")

    # 右侧连线
    s.poly([(CX + CW, 560), (1280, 560)], GREEN, 1.8, "gr")
    s.label(1180, 544, "简历原文（明文，不出网）", 12, "#166b3e", anchor="end")
    s.poly([(1280, 490), (1215, 490), (1215, 330), (CX + CW, 330)], GREEN, 1.8, "gr")
    s.label(1350, 512, "字段值 + 置信度", 12, "#166b3e")
    s.poly([(CX + CW, 300), (1245, 300), (1245, 690), (1280, 690)], FLOW, 1.7)
    s.label(1235, 400, "原始文件字节流", 12, FLOW_D, anchor="end")
    s.poly([(CX + CW, 340), (1225, 340), (1225, 900), (1280, 900)], FLOW, 1.7)
    s.label(1215, 780, "入队 import_task_id", 12, FLOW_D, anchor="end")
    s.poly([(CX + CW, 700), (1265, 700), (1265, 1100), (1280, 1100)], FLOW, 1.7)
    s.label(1255, 960, "任务进度 / 解析结果 / 留痕", 12, FLOW_D, anchor="end")
    s.poly([(CX + CW, 900), (1250, 900), (1250, 1450), (1280, 1450)], RED, 2.2, "rd")
    s.label(1240, 1180, "脱敏检索单元 → COL-01/02/03", 12.5, "#9c3434", weight="600", anchor="end")

    # 红线
    s.rect(70, 1700, 1670, 130, "#fffdf7", "#e8d9b0", rx=14, sw=1.4)
    s.dot(106, 1736, 8)
    s.text(126, 1736, "本图的四条实现要点", 19, "#8a6216", "700", "start")
    s.text(126, 1774, "① P3.5 是确定性加工：置信度与阈值比对决定是否入队，不由模型「感觉」决定　"
                       "② P3.6 脱敏必须在 P3.7 向量化之前（顺序反了向量库存明文，且无法事后修复）", 14.5, "#8a7a55", "400", "start")
    s.text(126, 1804, "③ 失败不阻塞整批：单个文件失败标记 parse_status=2 并记原因，整批继续　"
                       "④ P3.8 写留痕 change_source=0（AI 解析），与人工/合并/重解析区分", 14.5, "#8a7a55", "400", "start")
    return s


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
    print("OK", fig1().save(os.path.join(out, "AI数字化人才平台-数据流向总图.svg")))
    print("OK", fig2().save(os.path.join(out, "AI数字化人才平台-检索问答与出网边界.svg")))
    print("OK", fig3().save(os.path.join(out, "AI数字化人才平台-导入解析链路.svg")))
