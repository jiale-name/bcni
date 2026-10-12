# -*- coding: utf-8 -*-
"""把文档集 md 渲染为自包含 HTML（浅色主题）

Mermaid 图有两种产出模式：
  1) 预渲染（默认，推荐）：调用本机 Chromium 无头渲染，把图固化成【静态内联 SVG】写进 HTML。
     产出零 JS 依赖 —— 任何浏览器 / 预览面板打开都能看到图，不需要联网、不需要 CDN。
  2) 运行时渲染（--no-prerender）：内联本地 mermaid.min.js，页面加载时现场渲染。
     用于临时环境找不到 Chromium 的情况。

用法：
  python _build_html.py                     # 渲染 DOCS 全部文档（自动优先预渲染）
  python _build_html.py 架构设计.md          # 只渲染指定文档（可用文件名片段）
  python _build_html.py --no-prerender      # 强制走运行时渲染模式
  python _build_html.py --check             # 只做环境自检，不写文件

依赖（均为本地文件，无需联网）：
  assets/mermaid/mermaid.min.js   mermaid v10 UMD 单文件（自包含，无懒加载 chunk）
  <chromium>/chrome.exe           预渲染用；可用环境变量 MERMAID_CHROME 指定
"""
import os
import re
import sys
import json
import html
import shutil
import tempfile
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
# 文档源文件所在目录：优先 docs/（本项目结构），否则回退到脚本同目录
BASE = os.path.join(ROOT, 'docs') if os.path.isdir(os.path.join(ROOT, 'docs')) else ROOT

DOCS = [
    'AI数字化人才平台-需求分析文档.md',
    'AI数字化人才平台-需求评审报告.md',
    'AI数字化人才平台-需求评审报告-第二轮.md',
    'AI数字化人才平台-架构设计.md',
    'AI数字化人才平台-数据库设计.md',
    'AI数字化人才平台-接口设计.md',
]

MERMAID_JS = os.path.join(ROOT, 'assets', 'mermaid', 'mermaid.min.js')

CHROME_CANDIDATES = [
    os.environ.get('MERMAID_CHROME', ''),
    os.path.join(ROOT, 'assets', '_dl', 'chrome', 'chrome-win64', 'chrome.exe'),
    os.path.join(os.path.expanduser('~'), '.workbuddy', 'tools', 'chrome-win64', 'chrome.exe'),
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
]

FONT_STACK = '-apple-system,Segoe UI,Microsoft YaHei,PingFang SC,Helvetica,Arial,sans-serif'
# mermaid 默认字体，渲染后统一替换为文档字体，避免 SVG 内联样式盖掉中文渲染
DEFAULT_FONTS = ['"trebuchet ms",verdana,arial,sans-serif', '"trebuchet ms", verdana, arial, sans-serif']

CSS = """
:root{
  --bg:#ffffff; --fg:#1f2328; --muted:#6b7280; --line:#e5e7eb;
  --accent:#0b63d6; --accent-soft:#eff5ff; --code-bg:#f6f8fa;
  --warn:#c2410c; --danger:#b91c1c; --ok:#15803d;
}
*{box-sizing:border-box}
body{
  margin:0; padding:32px 24px 96px;
  background:var(--bg); color:var(--fg);
  font-family:-apple-system,"Segoe UI","Microsoft YaHei","PingFang SC",Helvetica,Arial,sans-serif;
  font-size:15px; line-height:1.75;
}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:28px;line-height:1.35;margin:8px 0 20px;padding-bottom:14px;border-bottom:3px solid var(--accent)}
h2{font-size:22px;margin:44px 0 14px;padding:8px 0 8px 12px;border-left:5px solid var(--accent);background:var(--accent-soft)}
h3{font-size:18px;margin:30px 0 10px;color:#111827}
h4{font-size:16px;margin:22px 0 8px;color:#374151}
p{margin:10px 0}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
hr{border:0;border-top:1px solid var(--line);margin:38px 0}
blockquote{
  margin:16px 0;padding:12px 16px;border-left:4px solid var(--accent);
  background:var(--accent-soft);color:#243b53;border-radius:0 6px 6px 0;
}
blockquote p{margin:6px 0}
table{border-collapse:collapse;width:100%;margin:16px 0;font-size:13.5px;display:block;overflow-x:auto}
thead th{
  background:#f1f5f9;color:#0f172a;font-weight:600;text-align:left;
  padding:9px 11px;border:1px solid var(--line);white-space:nowrap;
}
tbody td{padding:8px 11px;border:1px solid var(--line);vertical-align:top}
tbody tr:nth-child(even){background:#fafbfc}
tbody tr:hover{background:#f3f7ff}
tbody td:first-child{white-space:nowrap}
code{
  font-family:"JetBrains Mono",Consolas,"Courier New",monospace;
  background:var(--code-bg);padding:1.5px 5px;border-radius:4px;font-size:13px;
  border:1px solid #eaecef;
}
pre{
  background:var(--code-bg);border:1px solid var(--line);border-radius:8px;
  padding:14px 16px;overflow:auto;
}
pre code{background:none;border:0;padding:0;font-size:12.5px;line-height:1.6}
ul,ol{padding-left:24px}
li{margin:4px 0}
input[type=checkbox]{margin-right:6px}

/* ---- 图：预渲染静态 SVG ---- */
figure.diagram{
  margin:18px 0;padding:14px 12px 6px;border:1px solid var(--line);
  border-radius:10px;background:#fff;overflow-x:auto;
}
figure.diagram .svgbox{text-align:center}
figure.diagram svg{max-width:100%;height:auto;display:block;margin:0 auto}
figure.diagram details{margin-top:10px;border-top:1px dashed var(--line);padding-top:8px}
figure.diagram summary{
  cursor:pointer;font-size:12.5px;color:var(--muted);list-style:none;user-select:none;
}
figure.diagram summary::-webkit-details-marker{display:none}
figure.diagram summary:before{content:"▸ "}
figure.diagram details[open] summary:before{content:"▾ "}
figure.diagram details pre{margin:8px 0 4px;font-size:12px}

/* ---- 图：运行时渲染模式（--no-prerender）---- */
.mermaid{
  background:var(--code-bg);border:1px solid var(--line);border-radius:8px;
  padding:14px 16px;overflow:auto;white-space:pre-wrap;
  font-family:"JetBrains Mono",Consolas,"Courier New",monospace;font-size:12.5px;line-height:1.6;
}
.mermaid[data-processed="true"]{
  background:#fff;white-space:normal;padding:14px;text-align:center;border-radius:10px;
}
.mermaid svg{max-width:100%;height:auto}
.diag-note{
  margin:10px 0;padding:10px 14px;border:1px dashed var(--warn);border-radius:8px;
  background:#fff7ed;color:var(--warn);font-size:13px;
}
.toolbar{
  position:sticky;top:0;z-index:9;display:flex;gap:10px;justify-content:flex-end;
  padding:8px 0;background:linear-gradient(#fff 70%,rgba(255,255,255,0));
}
.toolbar span{font-size:12px;color:var(--muted);margin-right:auto;align-self:center}
"""

BADGE = {
    '🔴': ('#b91c1c', '600'),
    '🟠': ('#c2410c', '600'),
    '🟡': ('#a16207', '400'),
    '✅': ('#15803d', '600'),
    '⚠️': ('#c2410c', '600'),
    '❌': ('#b91c1c', '600'),
    '⚪': ('#6b7280', '400'),
}

MERMAID_CFG = {
    'startOnLoad': False,
    'theme': 'base',
    'securityLevel': 'loose',
    'fontFamily': FONT_STACK,
    'flowchart': {'htmlLabels': True, 'useMaxWidth': True},
    'sequence': {'useMaxWidth': True},
    'themeVariables': {
        'fontFamily': FONT_STACK,
        'fontSize': '14px',
        'primaryColor': '#eff5ff',
        'primaryBorderColor': '#0b63d6',
        'primaryTextColor': '#1f2328',
        'lineColor': '#6b7280',
        'secondaryColor': '#f0f9ff',
        'tertiaryColor': '#ffffff',
    },
}


# ---------------------------------------------------------------- 环境探测
def load_mermaid_js():
    if not os.path.exists(MERMAID_JS):
        return None
    with open(MERMAID_JS, 'r', encoding='utf-8') as f:
        return f.read()


def find_chrome():
    for p in CHROME_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


# ---------------------------------------------------------------- 预渲染
def prerender_svgs(sources, chrome, mm_js, keep_temp=False):
    """用无头 Chromium 把 mermaid 源码批量渲染成 SVG 字符串列表。

    做法：把「源码 + mermaid UMD」塞进一个临时页，页面用 mermaid.render() 逐张出图，
    再把 SVG 转义后写进 <pre class="out">；dump-dom 出来即可无损取回（不受嵌套 div 干扰）。
    返回 (svgs, err)；失败返回 (None, 原因)。
    """
    payload = json.dumps(sources, ensure_ascii=False).replace('<', '\\u003c')
    page = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>prerender</title>
<style>body{margin:0;font-family:%(font)s}</style></head>
<body>
<div id="stage"></div>
<script>%(mm)s</script>
<script>
window.__RESULT__ = null;
(async function(){
  var SRC = %(src)s;
  var out = [];
  try {
    mermaid.initialize(%(cfg)s);
    for (var i = 0; i < SRC.length; i++) {
      try {
        var r = await mermaid.render('mmd' + i, SRC[i]);
        out.push(r.svg);
      } catch (e) {
        out.push('__ERR__' + (e && e.message ? e.message : String(e)));
      }
    }
  } catch (e) {
    out.push('__ERR__' + (e && e.message ? e.message : String(e)));
  }
  var esc = function(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); };
  document.getElementById('stage').innerHTML = out.map(function(s, i){
    return '<pre class="out" data-i="' + i + '">' + esc(s) + '</pre>';
  }).join('');
  window.__RESULT__ = out.length;
})();
</script>
</body></html>""" % {
        'font': FONT_STACK,
        'mm': mm_js,
        'src': payload,
        'cfg': json.dumps(MERMAID_CFG),
    }

    # 临时页写入系统临时目录（不污染仓库；部分沙箱环境下删除可能被拦截，也无所谓）
    tmp = os.path.join(tempfile.gettempdir(), '_prerender.tmp.html')
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(page)

    cmd = [
        chrome,
        '--headless=new', '--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage',
        '--hide-scrollbars', '--force-device-scale-factor=1',
        '--virtual-time-budget=30000', '--run-all-compositor-stages-before-draw',
        '--allow-file-access-from-files', '--disable-features=Translate,MediaRouter',
        '--dump-dom', 'file:///' + tmp.replace('\\', '/'),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=180)
    except Exception as e:
        return None, '启动 Chromium 失败: %s' % e
    if not keep_temp:
        try:
            os.remove(tmp)
        except OSError:
            pass

    dom = r.stdout.decode('utf-8', 'replace')
    found = re.findall(r'<pre class="out" data-i="(\d+)">(.*?)</pre>', dom, flags=re.S)
    if len(found) != len(sources):
        return None, '只取回 %d/%d 张图（rc=%s, stderr=%s）' % (
            len(found), len(sources), r.returncode,
            r.stderr.decode('utf-8', 'replace')[-300:])

    svgs = [None] * len(sources)
    errs = []
    for idx, body in found:
        svg = html.unescape(body)
        i = int(idx)
        if svg.startswith('__ERR__'):
            errs.append('#%d %s' % (i, svg[7:120]))
            svg = ''
        else:
            for d in DEFAULT_FONTS:
                svg = svg.replace(d, FONT_STACK)
        svgs[i] = svg
    return svgs, ('渲染告警: ' + ' | '.join(errs) if errs else '')


# ---------------------------------------------------------------- 组装
MERMAID_BLOCK_RE = re.compile(
    r'<pre><code class="language-mermaid">(.*?)</code></pre>', flags=re.S)


def decode_sources(body):
    """取出所有 mermaid 源码（已还原 HTML 实体）。"""
    return [html.unescape(m.group(1)) for m in MERMAID_BLOCK_RE.finditer(body)]


def inject_static_svgs(body, svgs):
    """把 mermaid 代码块替换为「静态 SVG + 源码折叠」的 figure。"""
    state = {'i': 0}

    def repl(m):
        i = state['i']
        state['i'] += 1
        src = html.unescape(m.group(1)).strip('\n')
        svg = svgs[i] if i < len(svgs) else ''
        if not svg:
            return ('<div class="diag-note">⚠️ 该图预渲染失败，已保留源码：</div>'
                    '<pre><code>%s</code></pre>' % html.escape(src))
        return (
            '<figure class="diagram">\n'
            '<div class="svgbox">%s</div>\n'
            '<details><summary>查看 Mermaid 源码</summary>\n'
            '<pre><code>%s</code></pre>\n'
            '</details>\n'
            '</figure>' % (svg, html.escape(src))
        )

    return MERMAID_BLOCK_RE.sub(repl, body)


def inject_live_blocks(body):
    """运行时渲染模式：代码块 → 可渲染 div。"""
    return MERMAID_BLOCK_RE.sub(lambda m: '<div class="mermaid">%s</div>' % m.group(1), body)


def boot_script_inline(mm_js, n):
    """把 mermaid UMD 直接内联进 HTML —— 无网络、无相对路径、无 ESM/CORS 依赖。"""
    return (
        '<script>%s</script>\n'
        '<script>\n'
        '(function(){\n'
        '  function fail(msg){\n'
        '    var b=document.createElement("div");b.className="diag-note";\n'
        '    b.textContent="⚠️ Mermaid 渲染失败：" + msg + "（可展开下方源码阅读）";\n'
        '    var boxes=document.querySelectorAll(".mermaid");\n'
        '    if(boxes.length){boxes[0].parentNode.insertBefore(b,boxes[0]);}\n'
        '  }\n'
        '  try{\n'
        '    if(!window.mermaid){return fail("mermaid 未加载");}\n'
        '    mermaid.initialize(%s);\n'
        '    var p=mermaid.run({querySelector:".mermaid"});\n'
        '    if(p&&p.catch){p.catch(function(e){fail(e&&e.message?e.message:e);});}\n'
        '  }catch(e){fail(e&&e.message?e.message:e);}\n'
        '})();\n'
        '</script>\n'
    ) % (mm_js, json.dumps(MERMAID_CFG))


def build_doc(title, body, boot):
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title>
<style>%s</style>
</head>
<body>
<div class="wrap">
<div class="toolbar"><span>%s</span></div>
%s
</div>
%s</body>
</html>
""" % (title, CSS, title, body, boot)


# ---------------------------------------------------------------- 主流程
def render(md_name, prerender=True, verbose=True):
    md_path = os.path.join(BASE, md_name)
    html_path = os.path.splitext(md_path)[0] + '.html'
    with open(md_path, 'r', encoding='utf-8') as f:
        text = f.read()

    body = markdown.markdown(
        text,
        extensions=['tables', 'fenced_code', 'toc', 'sane_lists', 'attr_list'],
    )
    for k, (color, weight) in BADGE.items():
        body = body.replace(k, '<span style="color:%s;font-weight:%s">%s</span>' % (color, weight, k))

    title = md_name.replace('.md', '')
    boot = ''
    svgs = None
    sources = decode_sources(body)

    if sources:
        chrome = find_chrome() if prerender else None
        mm_js = load_mermaid_js()
        if chrome:
            svgs, err = prerender_svgs(sources, chrome, mm_js)
            if svgs is None:
                print('  ! 预渲染失败：%s' % err)
        elif prerender:
            print('  ! 未找到 Chromium，回退运行时渲染模式')

        if svgs is not None:
            body = inject_static_svgs(body, svgs)
            if verbose:
                print('  · %d 张图已固化为静态 SVG' % len(sources))
            if err:
                print('  ' + err)
        else:
            body = inject_live_blocks(body)
            if not mm_js:
                print('  ! 缺少 %s，图表将只显示源码' % MERMAID_JS)
                boot = ('<div class="diag-note">⚠️ 离线渲染组件缺失，'
                        '请运行 python _build_html.py 重新生成。</div>\n')
            else:
                boot = boot_script_inline(mm_js, len(sources))
                print('  · %d 张图改为运行时渲染（内联 mermaid，无 CDN）' % len(sources))
    elif prerender:
        pass

    doc = build_doc(title, body, boot)
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(doc)
    return html_path, len(doc)


def pick_docs(argv):
    """支持用文件名片段匹配文档。"""
    keys = [a for a in argv if not a.startswith('-')]
    if not keys:
        return DOCS
    out = []
    for k in keys:
        if os.path.isabs(k) or os.path.exists(os.path.join(BASE, k)):
            n = k if k.endswith('.md') else k + '.md'
            out.append(os.path.relpath(n, BASE) if os.path.isabs(n) else n)
            continue
        hit = [d for d in DOCS if k in d]
        if not hit:
            cand = [f for f in os.listdir(BASE) if f.endswith('.md') and k in f]
            hit = cand
        out.extend(hit or [k if k.endswith('.md') else k + '.md'])
    return out


def main():
    argv = sys.argv[1:]

    if '--check' in argv:
        mm = load_mermaid_js()
        ch = find_chrome()
        print('mermaid UMD :', MERMAID_JS if mm else '缺失')
        print('  大小      :', ('%.1f MB' % (len(mm) / 1048576)) if mm else '-')
        print('chromium    :', ch or '未找到（将回退运行时渲染）')
        return

    prerender = '--no-prerender' not in argv
    for name in pick_docs(argv):
        if not os.path.exists(os.path.join(BASE, name)):
            print('SKIP 找不到', name)
            continue
        print('BUILD', name)
        p, n = render(name, prerender=prerender)
        print('OK   ', p, '(%.0f KB)' % (n / 1024))


if __name__ == '__main__':
    try:
        import markdown  # noqa: F401
    except ImportError:
        print('NEED_INSTALL: pip install markdown')
        sys.exit(2)
    main()
