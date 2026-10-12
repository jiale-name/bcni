# -*- coding: utf-8 -*-
"""生成「2026 顶配版 AI 数字化人才平台」一期全站原型（单文件 HTML，零外部依赖）。

规则（对齐 design-doc-suite/references/f-prototype.md）：
  1. 单文件 HTML，CSS 全内联，不引 CDN —— 离线可直接预览；
  2. 画布 1440px 居中，左侧栏 224px + 顶栏 56px + 主内容区；
  3. 假数据用真实感数据，禁用 xxx / test1；
  4. 列表页覆盖五态：默认 / 加载 / 空 / 错误 / 无权限；
  5. 页面之间用相对链接互跳，nav 链接指向真实文件。

用法： python _gen_prototype.py
输出： prototype/pNN-*.html + prototype/index.html
"""
import io
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'prototype')

# ============================================================ 设计系统（唯一来源）
CSS = """
:root{
  --bg:#f3f6f7; --panel:#ffffff; --line:#d9e3e6; --line-2:#ecf1f2;
  --txt:#18262d; --txt-2:#52636a; --txt-3:#84939a;
  --primary:#175d75; --primary-weak:#e5f2f4; --primary-deep:#0f465b;
  --danger:#c8474f; --danger-weak:#fcedee;
  --warn:#b76b16; --warn-weak:#fff5e2;
  --ok:#16836a; --ok-weak:#e6f5ef;
  --info:#526da6; --info-weak:#eff2fb;
  --nav-bg:#e9f4f3; --nav-line:#c9e1df; --nav-hover:#dff0ef;
  --nav-active:#c5e6e5; --nav-active-txt:#0f6670; --nav-group:#6b8588;
  --radius:6px; --sidebar:224px; --topbar:56px;
  --shadow:0 1px 3px rgba(24,47,56,.07);
  --mono:ui-monospace,Consolas,"Courier New",monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:-apple-system,"Segoe UI","Microsoft YaHei","PingFang SC",sans-serif;
  background:var(--bg); color:var(--txt); font-size:14px; line-height:1.55;
}
a{color:var(--primary);text-decoration:none}
a:hover{text-decoration:underline}
.mark{display:inline-flex;align-items:center;justify-content:center;width:17px;height:17px;
  border-radius:50%;background:var(--danger);color:#fff;font-size:11px;font-weight:700;
  margin-left:5px;vertical-align:1px;flex:0 0 auto}
/* ===== 布局 ===== */
.app{display:flex;max-width:1440px;margin:0 auto;min-height:100vh;background:var(--panel);
  box-shadow:0 1px 14px rgba(24,47,56,.07)}
.sidebar{width:var(--sidebar);flex:0 0 var(--sidebar);border-right:1px solid var(--line);
  display:flex;flex-direction:column;background:var(--nav-bg);border-right-color:var(--nav-line)}
.brand{height:var(--topbar);display:flex;align-items:center;gap:9px;padding:0 16px;
  border-bottom:1px solid var(--nav-line);font-weight:700;font-size:14px;letter-spacing:.2px}
.brand .dot{width:22px;height:22px;border-radius:5px;background:#168c92;color:#fff;
  font-size:12px;display:flex;align-items:center;justify-content:center;flex:0 0 auto}
.brand small{font-weight:400;font-size:11px;color:var(--txt-3);margin-left:2px}
.nav{padding:8px 8px 14px;flex:1;overflow-y:auto}
.nav .grp{font-size:11px;color:var(--nav-group);padding:12px 12px 5px;letter-spacing:.6px}
.nav a{display:flex;align-items:center;gap:9px;padding:8px 11px;border-radius:5px;
  color:#46656a;font-size:13.5px;margin-bottom:1px}
.nav a:hover{background:var(--nav-hover);color:#1a5b63;text-decoration:none}
.nav a.active{background:var(--nav-active);color:var(--nav-active-txt);font-weight:600;
  box-shadow:inset 3px 0 0 #168c92}
.nav a.active .ico{color:#168c92;opacity:1}
.nav .ico{width:15px;text-align:center;opacity:.8;font-size:13px;flex:0 0 auto}
.nav .lock{margin-left:auto;font-size:10.5px;color:var(--nav-group)}
.userbox{border-top:1px solid var(--nav-line);padding:11px 14px;display:flex;gap:8px;
  align-items:center;font-size:12.5px;color:var(--txt-2)}
.avatar{width:27px;height:27px;border-radius:50%;background:#d4edeb;
  color:#0f6670;display:flex;align-items:center;justify-content:center;
  font-size:12px;font-weight:700;flex:0 0 auto}
/* ===== 主区 ===== */
.main{flex:1;min-width:0;display:flex;flex-direction:column}
.topbar{height:var(--topbar);flex:0 0 var(--topbar);border-bottom:1px solid var(--line);
  display:flex;align-items:center;justify-content:space-between;padding:0 20px;gap:12px}
.crumb{font-size:12.5px;color:var(--txt-3);min-width:0}
.crumb b{color:var(--txt);font-weight:600;font-size:15px;margin-right:8px}
.topbar .acts{display:flex;gap:10px;align-items:center;font-size:13px;color:var(--txt-2);flex:0 0 auto}
.pbadge{background:var(--warn-weak);color:var(--warn);border:1px solid #f0e0b8;
  border-radius:4px;padding:1px 7px;font-size:11.5px;font-weight:600}
.content{padding:16px 20px 30px}
/* ===== 工具条 ===== */
.toolbar{display:flex;gap:9px;align-items:center;margin-bottom:13px;flex-wrap:wrap}
.toolbar .grow{flex:1}
input[type=text],input[type=password],input[type=number],select,textarea,.input{
  height:32px;padding:0 10px;border:1px solid var(--line);border-radius:5px;background:#fff;
  color:var(--txt);font-size:13px;outline:none;font-family:inherit}
input[type=text],input[type=password],textarea{min-width:176px}
input::placeholder,textarea::placeholder{color:var(--txt-3)}
input:focus,select:focus,textarea:focus{border-color:var(--primary);box-shadow:0 0 0 2px var(--primary-weak)}
textarea{height:auto;padding:8px 10px;line-height:1.6;resize:vertical}
select{min-width:120px;cursor:pointer}
.btn{height:32px;padding:0 13px;border:1px solid var(--line);border-radius:5px;background:#fff;
  color:var(--txt);font-size:13px;cursor:pointer;display:inline-flex;align-items:center;
  gap:5px;font-family:inherit;white-space:nowrap}
.btn:hover{border-color:var(--primary);color:var(--primary)}
.btn-primary{background:var(--primary);border-color:var(--primary);color:#fff}
.btn-primary:hover{background:var(--primary-deep);color:#fff}
.btn-danger{color:var(--danger);border-color:#eccaca}
.btn-danger:hover{background:var(--danger-weak);border-color:var(--danger);color:var(--danger)}
.btn-text{border:none;padding:0 5px;color:var(--primary);background:none;height:auto}
.btn-text:hover{text-decoration:underline;color:var(--primary)}
.btn-sm{height:26px;padding:0 9px;font-size:12.5px}
.btn[disabled]{opacity:.5;cursor:not-allowed}
/* ===== 卡片 ===== */
.card{background:#fff;border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;
  box-shadow:var(--shadow);margin-bottom:14px}
.card-hd{display:flex;align-items:center;justify-content:space-between;gap:10px;
  padding:11px 14px;border-bottom:1px solid var(--line);background:#fbfdfc}
.card-hd h3{font-size:13.5px;font-weight:600}
.card-hd .sub{font-size:12px;color:var(--txt-3);font-weight:400;margin-left:8px}
.card-bd{padding:14px}
.grid{display:grid;gap:14px}
.g2{grid-template-columns:repeat(2,minmax(0,1fr))}
.g3{grid-template-columns:repeat(3,minmax(0,1fr))}
.g4{grid-template-columns:repeat(4,minmax(0,1fr))}
/* ===== KPI ===== */
.stat{background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:13px 15px;
  box-shadow:var(--shadow)}
.stat .lbl{font-size:12.5px;color:var(--txt-2);display:flex;align-items:center;gap:5px}
.stat .num{font-size:25px;font-weight:700;margin-top:5px;letter-spacing:-.4px;
  font-variant-numeric:tabular-nums}
.stat .num small{font-size:13px;font-weight:600;color:var(--txt-2);margin-left:2px}
.stat .sub{font-size:12px;color:var(--txt-3);margin-top:3px}
.stat .up{color:var(--danger)}
.stat .down{color:var(--ok)}
/* ===== 表格 ===== */
table{width:100%;border-collapse:collapse;font-size:13px}
thead th{background:#f8fbfa;text-align:left;font-weight:600;color:var(--txt-2);
  padding:10px 13px;border-bottom:1px solid var(--line);white-space:nowrap}
tbody td{padding:11px 13px;border-bottom:1px solid var(--line-2);vertical-align:middle}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover{background:#fafcff}
td.nowrap,th.nowrap{white-space:nowrap}
td.num{font-variant-numeric:tabular-nums;text-align:right}
.ell{max-width:230px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block}
.muted{color:var(--txt-3)}
.mono{font-family:var(--mono);font-size:12.5px}
.ops{white-space:nowrap}
.ops a{margin-right:9px}
.ops a:last-child{margin-right:0}
/* ===== 标签 ===== */
.tag{display:inline-block;padding:1px 8px;border-radius:9px;font-size:12px;line-height:17px;
  white-space:nowrap}
.tag-ok{background:var(--ok-weak);color:var(--ok)}
.tag-warn{background:var(--warn-weak);color:var(--warn)}
.tag-danger{background:var(--danger-weak);color:var(--danger)}
.tag-info{background:var(--info-weak);color:var(--info)}
.tag-gray{background:#f2f4f7;color:var(--txt-2)}
.tag-line{background:#fff;border:1px solid var(--line);color:var(--txt-2)}
.chip{display:inline-flex;align-items:center;gap:4px;background:var(--primary-weak);
  color:var(--primary);border-radius:4px;padding:2px 8px;font-size:12px;margin:0 5px 5px 0}
.chip .x{opacity:.55;font-size:13px;cursor:pointer}
/* ===== 分页 ===== */
.pager{display:flex;justify-content:space-between;align-items:center;padding:11px 14px;
  border-top:1px solid var(--line);font-size:12.5px;color:var(--txt-2);flex-wrap:wrap;gap:8px}
.pages{display:flex;gap:5px;align-items:center}
.pages span{min-width:27px;height:27px;padding:0 7px;border:1px solid var(--line);border-radius:5px;
  display:flex;align-items:center;justify-content:center;cursor:pointer;background:#fff;font-size:12.5px}
.pages span.cur{background:var(--primary);border-color:var(--primary);color:#fff}
/* ===== 状态页 ===== */
.state-wrap{border:1px dashed var(--line);border-radius:var(--radius);padding:42px 20px;
  text-align:center;color:var(--txt-2);background:#fff}
.state-wrap .big{font-size:32px;margin-bottom:9px;line-height:1}
.state-wrap h4{font-size:15px;color:var(--txt);margin-bottom:5px}
.state-wrap p{font-size:12.5px;color:var(--txt-3);margin-bottom:13px}
.skeleton{height:13px;border-radius:4px;background:linear-gradient(90deg,#f0f2f5,#e6e9ee,#f0f2f5);
  background-size:200% 100%;animation:sk 1.2s linear infinite;margin:11px 0}
@keyframes sk{from{background-position:200% 0}to{background-position:-200% 0}}
/* ===== 标注 ===== */
.legend{margin-top:6px;border-top:1px dashed var(--line);padding-top:11px;font-size:12.5px;
  color:var(--txt-3);line-height:1.9}
.legend b{color:var(--txt-2)}
.state-box{margin-top:16px;border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}
.state-box>header{background:#f8fbfa;padding:8px 13px;border-bottom:1px solid var(--line);
  font-size:12px;color:var(--txt-2);font-family:var(--mono)}
.state-box .body{padding:14px}
/* ===== 提示条 ===== */
.alert{display:flex;gap:9px;align-items:flex-start;padding:10px 13px;border-radius:var(--radius);
  font-size:13px;margin-bottom:13px;line-height:1.6}
.alert .i{flex:0 0 auto;font-weight:700}
.alert-info{background:var(--info-weak);color:#2f4a80;border:1px solid #d6e0f5}
.alert-warn{background:var(--warn-weak);color:#8a6216;border:1px solid #f0e2ba}
.alert-danger{background:var(--danger-weak);color:#9c3434;border:1px solid #f2cfcf}
.alert-ok{background:var(--ok-weak);color:#166b3e;border:1px solid #cbe8da}
.alert a{font-weight:600}
/* ===== 表单 ===== */
.form-row{display:flex;gap:14px;margin-bottom:14px;align-items:flex-start}
.form-row>label{flex:0 0 132px;text-align:right;padding-top:7px;font-size:13px;color:var(--txt-2)}
.form-row>label .req{color:var(--danger);margin-right:3px}
.form-row .ctl{flex:1;min-width:0}
.form-help{font-size:12px;color:var(--txt-3);margin-top:5px}
.radio,.check{display:inline-flex;align-items:center;gap:6px;margin-right:16px;font-size:13px;cursor:pointer}
/* ===== 上传区 ===== */
.drop{border:1.5px dashed #c7d3e4;border-radius:var(--radius);background:#fbfdff;
  padding:30px 20px;text-align:center}
.drop .ic{font-size:30px;color:var(--primary);opacity:.8;margin-bottom:8px}
.drop h4{font-size:14px;margin-bottom:5px}
.drop p{font-size:12.5px;color:var(--txt-3);margin-bottom:12px}
/* ===== 步骤条 ===== */
.steps{display:flex;align-items:center;gap:0;margin:4px 0 18px;flex-wrap:wrap}
.step{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--txt-3)}
.step .n{width:22px;height:22px;border-radius:50%;border:1px solid var(--line);background:#fff;
  display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600;flex:0 0 auto}
.step.done{color:var(--ok)} .step.done .n{background:var(--ok-weak);border-color:#bfe3d0;color:var(--ok)}
.step.cur{color:var(--primary);font-weight:600}
.step.cur .n{background:var(--primary);border-color:var(--primary);color:#fff}
.step-line{flex:1;height:1px;background:var(--line);margin:0 12px;min-width:20px}
/* ===== 进度条 ===== */
.progress{height:7px;border-radius:4px;background:#eef1f6;overflow:hidden}
.progress>i{display:block;height:100%;background:var(--primary);border-radius:4px}
.progress.ok>i{background:var(--ok)}
.progress.warn>i{background:var(--warn)}
/* ===== 并排对比（复核 / 合并） ===== */
.diff{display:grid;grid-template-columns:1fr 1fr;gap:0;border:1px solid var(--line);
  border-radius:var(--radius);overflow:hidden;background:#fff}
.diff>div{padding:12px 14px;font-size:13px}
.diff>div:first-child{border-right:1px solid var(--line);background:#fbfdfc}
.diff h5{font-size:12px;color:var(--txt-3);font-weight:600;margin-bottom:8px;
  display:flex;justify-content:space-between;align-items:center}
.diff p{margin-bottom:8px;line-height:1.7}
.diff p:last-child{margin-bottom:0}
.hl{background:var(--warn-weak);border-bottom:1px solid #eddcae;padding:0 2px;border-radius:2px}
.hl-ok{background:var(--ok-weak);border-bottom:1px solid #c3e5d3;padding:0 2px}
/* ===== 时间线（变更留痕） ===== */
.timeline{position:relative;padding-left:22px}
.timeline:before{content:"";position:absolute;left:5px;top:5px;bottom:5px;width:1px;background:var(--line)}
.tl-item{position:relative;padding-bottom:15px}
.tl-item:last-child{padding-bottom:0}
.tl-item:before{content:"";position:absolute;left:-21px;top:5px;width:9px;height:9px;border-radius:50%;
  background:#fff;border:2px solid var(--primary)}
.tl-item.ai:before{border-color:var(--info)}
.tl-item.merge:before{border-color:var(--warn)}
.tl-head{font-size:13px;display:flex;gap:9px;align-items:baseline;flex-wrap:wrap}
.tl-head .who{color:var(--txt-2)}
.tl-body{font-size:12.5px;color:var(--txt-2);margin-top:4px;line-height:1.8}
.tl-body .old{color:var(--txt-3);text-decoration:line-through}
.tl-body .new{color:var(--ok);font-weight:600}
/* ===== 问答（对话流） ===== */
.chat{background:#f5f9f9;border:1px solid var(--line);border-radius:var(--radius);padding:14px;
  min-height:300px}
.msg{display:flex;gap:9px;margin-bottom:16px}
.msg .av{width:26px;height:26px;border-radius:5px;flex:0 0 auto;display:flex;align-items:center;
  justify-content:center;font-size:11.5px;font-weight:700}
.msg.user{flex-direction:row-reverse}
.msg.user .av{background:var(--primary);color:#fff}
.msg.ai .av{background:var(--ok-weak);color:var(--ok)}
.msg .bub{max-width:76%;background:#fff;border:1px solid var(--line);border-radius:var(--radius);
  padding:10px 13px;font-size:13px;line-height:1.75}
.msg.user .bub{background:var(--primary-weak);border-color:#d3e2fa}
.msg .bub p{margin-bottom:7px}
.msg .bub p:last-child{margin-bottom:0}
.msg .meta{font-size:11.5px;color:var(--txt-3);margin-top:7px}
.ph{background:#f2f4f7;border:1px solid var(--line);border-radius:3px;padding:0 4px;
  font-family:var(--mono);font-size:12px;color:#7a6a2f}
.cite{background:#fbfdff;border:1px solid var(--line);border-left:3px solid var(--ok);
  border-radius:4px;padding:9px 11px;margin-top:9px;font-size:12.5px}
.cite .hd{display:flex;justify-content:space-between;gap:8px;color:var(--txt-2);margin-bottom:4px}
.cite .txt{color:var(--txt-2);line-height:1.7}
.compose{display:flex;gap:9px;align-items:flex-end;margin-top:12px}
.compose textarea{flex:1}
/* ===== 上传文件列表 / 明细 ===== */
.filelist{border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}
.filelist .fi{display:flex;align-items:center;gap:10px;padding:9px 13px;font-size:13px;
  border-bottom:1px solid var(--line-2)}
.filelist .fi:last-child{border-bottom:none}
.filelist .fi .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
/* ===== 图（内联 SVG 容器） ===== */
.chart{width:100%;overflow-x:auto}
.chart svg{display:block;margin:0 auto}
/* ===== 登录页 ===== */
.login-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:linear-gradient(135deg,#eaf4f4 0%,#f7fafb 55%,#e8f1f5 100%);padding:30px}
.login-box{width:392px;background:#fff;border:1px solid var(--line);border-radius:10px;
  box-shadow:0 12px 40px rgba(24,47,56,.11);padding:32px 30px 26px}
.login-box .lg{display:flex;align-items:center;gap:10px;margin-bottom:6px}
.login-box .lg .dot{width:32px;height:32px;border-radius:8px;background:var(--primary);color:#fff;
  display:flex;align-items:center;justify-content:center;font-weight:700;font-size:15px}
.login-box h1{font-size:17px;margin-bottom:3px}
.login-box .pjt{font-size:12.5px;color:var(--txt-3);margin-bottom:22px}
.login-box .fi{margin-bottom:15px}
.login-box label{display:block;font-size:13px;color:var(--txt-2);margin-bottom:6px}
.login-box input{width:100%;height:38px;font-size:14px}
.login-box .btn-login{width:100%;height:38px;justify-content:center;font-size:14px;margin-top:4px}
.login-foot{margin-top:20px;border-top:1px dashed var(--line);padding-top:12px;font-size:12px;
  color:var(--txt-3);line-height:1.9}
.errline{display:none;background:var(--danger-weak);border:1px solid #f2cfcf;color:#9c3434;
  border-radius:5px;padding:8px 11px;font-size:12.5px;margin-bottom:14px}
/* ===== index 评审入口 ===== */
.index-wrap{max-width:1180px;margin:0 auto;padding:30px 22px 70px}
.idx-hd{border-bottom:2px solid var(--primary);padding-bottom:16px;margin-bottom:22px}
.idx-hd h1{font-size:23px;letter-spacing:-.3px}
.idx-hd p{color:var(--txt-2);font-size:13.5px;margin-top:7px}
.idx-hd .tags{margin-top:12px;display:flex;gap:7px;flex-wrap:wrap}
.pgrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:8px}
.pcard{display:block;background:#fff;border:1px solid var(--line);border-radius:var(--radius);
  padding:13px 14px;box-shadow:var(--shadow);color:var(--txt)}
.pcard:hover{border-color:var(--primary);text-decoration:none;box-shadow:0 3px 12px rgba(27,95,217,.11)}
.pcard .no{font-size:11.5px;color:var(--txt-3);font-family:var(--mono)}
.pcard .nm{font-size:14px;font-weight:600;margin:3px 0 5px}
.pcard .rt{font-family:var(--mono);font-size:11.5px;color:var(--primary);
  background:var(--primary-weak);border-radius:3px;padding:1px 5px}
.pcard .fp{font-size:11.5px;color:var(--txt-3);margin-top:7px;line-height:1.7}
.st-cov{font-size:11.5px;color:var(--txt-2);margin-top:6px}
.st-cov i{font-style:normal;color:var(--ok)}

/* ===== 体验优化：焦点、导航和响应式布局 ===== */
html{scroll-behavior:smooth}
body{min-width:0;overflow-x:hidden}
button,input,select,textarea{font:inherit}
button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,
textarea:focus-visible{outline:3px solid rgba(27,95,217,.28);outline-offset:2px}
.skip-link{position:fixed;left:12px;top:-48px;z-index:300;background:var(--primary);
  color:#fff;padding:9px 13px;border-radius:0 0 5px 5px;font-size:13px;transition:top .16s}
.skip-link:focus{top:0;color:#fff;text-decoration:none}
.brand{position:relative}
.brand-copy{display:flex;flex-direction:column;min-width:0;line-height:1.25}
.brand-copy b{font-size:14px;font-weight:700;white-space:nowrap}
.brand-copy small{margin-left:0;white-space:nowrap}
.sidebar-toggle,.mobile-nav-toggle{border:1px solid var(--line);background:#fff;color:var(--txt-2);
  width:28px;height:28px;border-radius:5px;display:inline-flex;align-items:center;
  justify-content:center;cursor:pointer;line-height:1}
.sidebar-toggle{margin-left:auto;font-size:16px}
.sidebar .sidebar-toggle{background:#f4fbfb;border-color:#b9d9d7;color:#33727a}
.sidebar-toggle:hover,.mobile-nav-toggle:hover{border-color:var(--primary);color:var(--primary)}
.mobile-nav-toggle{display:none;flex:0 0 auto;font-size:17px}
.nav a{min-height:34px}
.nav a .lbl{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.userbox .user-meta{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nav-backdrop{display:none}
.topbar{position:sticky;top:0;background:rgba(255,255,255,.96);backdrop-filter:blur(8px)}
.content{scroll-margin-top:var(--topbar)}
.toolbar .btn,.toolbar input,.toolbar select{min-height:32px}
.btn,.pages span{transition:background-color .14s,border-color .14s,color .14s,transform .14s}
.btn:active,.pages span:active{transform:translateY(1px)}
.card-hd h3{line-height:1.45}
.card:has(> table),.card:has(.tb){overflow-x:auto}
.card:has(> table) .card-hd,.card:has(.tb) .card-hd{min-width:640px}
.card:has(> table)>table,.card:has(.tb) table{min-width:640px}
.sidebar-collapsed .sidebar{width:64px;flex-basis:64px}
.sidebar-collapsed .brand{justify-content:center;padding:0}
.sidebar-collapsed .brand-copy,.sidebar-collapsed .sidebar-toggle,
.sidebar-collapsed .nav .grp,.sidebar-collapsed .nav .lbl,
.sidebar-collapsed .nav .lock,.sidebar-collapsed .user-meta{display:none}
.sidebar-collapsed .nav a{justify-content:center;padding-left:0;padding-right:0}
.sidebar-collapsed .userbox{justify-content:center;padding-left:0;padding-right:0}
.sidebar-collapsed .nav .ico{width:auto}
@media (max-width:1120px){
  .topbar{padding-left:16px;padding-right:16px}
  .content{padding-left:16px;padding-right:16px}
  .topbar .acts{gap:7px;font-size:12px}
}
@media (max-width:760px){
  .app{display:block;min-height:100vh}
  .sidebar{position:fixed;inset:0 auto 0 0;width:min(286px,86vw);flex:none;
    transform:translateX(-102%);transition:transform .22s ease;box-shadow:0 12px 30px rgba(24,47,56,.18);z-index:120}
  .brand{padding:0 16px;justify-content:flex-start}
  .brand-copy,.sidebar-toggle,.nav .grp,.nav .lbl,.nav .lock,.user-meta{display:block}
  .brand .sidebar-toggle{display:none}
  .nav a{justify-content:flex-start;padding:8px 11px}
  .nav .ico{width:15px}
  .userbox{justify-content:flex-start;padding:11px 14px}
  body.nav-open{overflow:hidden}
  body.nav-open .sidebar{transform:none}
  .nav-backdrop{position:fixed;inset:0;border:0;background:rgba(24,47,56,.38);z-index:110;cursor:pointer}
  body.nav-open .nav-backdrop{display:block}
  .mobile-nav-toggle{display:inline-flex}
  .topbar{height:auto;min-height:var(--topbar);padding:9px 12px;align-items:center}
  .crumb{display:flex;align-items:center;gap:7px;line-height:1.35}
  .crumb b{font-size:14px;margin-right:0}
  .topbar .acts{margin-left:auto}
  .topbar .acts>span:not(.pbadge),.topbar .acts>.muted,.topbar .acts>a{display:none}
  .content{padding:13px 12px 28px}
  .toolbar{gap:7px}
  .toolbar input[type=text],.toolbar input[type=password],.toolbar input[type=number],
  .toolbar select,.toolbar .grow{width:100%;min-width:0;flex:1 1 100%}
  .toolbar .btn{flex:0 0 auto}
  .form-row{display:block}
  .form-row>label{display:block;text-align:left;padding-top:0;margin-bottom:6px}
  .login-wrap{padding:16px}
  .index-wrap{padding:22px 14px 44px}
  .idx-hd h1{font-size:20px}
  .pgrid{grid-template-columns:1fr}
}
@media (max-width:560px){
  .stats,.g2,.g3,.g4,.diff{grid-template-columns:1fr}
  .card-hd{align-items:flex-start;padding:10px 12px}
  .card-bd{padding:12px}
  .card-hd .sub{display:block;margin-left:0;margin-top:2px}
  .pages{width:100%;overflow-x:auto;padding-bottom:2px}
  .pager{align-items:flex-start}
  .steps{align-items:flex-start;flex-direction:column;gap:8px}
  .step-line{width:1px;height:12px;flex:0 0 auto;margin:0 0 0 10px}
  .msg .bub{max-width:88%}
  .login-box{padding:26px 20px 22px}
}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{scroll-behavior:auto!important;animation-duration:.01ms!important;
    animation-iteration-count:1!important;transition-duration:.01ms!important}
}

/* 兼容页面内少量历史内联色值，确保整套原型使用同一组色彩语义。 */
[style*="#5b6673"]{color:var(--txt-2)!important}
[style*="#98a2ae"]{color:var(--txt-3)!important}
[style*="#e3e8ef"]{border-color:var(--line)!important}
[style*="#eef2f7"]{background:var(--line-2)!important;border-color:var(--line-2)!important}
[style*="#eaf1fd"]{background:var(--primary-weak)!important;color:var(--primary)!important}
[style*="#1b5fd9"]{color:var(--primary)!important;fill:var(--primary)!important;stroke:var(--primary)!important}
[style*="#1f9254"]{color:var(--ok)!important;fill:var(--ok)!important;stroke:var(--ok)!important}
[style*="#b7791f"]{color:var(--warn)!important;fill:var(--warn)!important;stroke:var(--warn)!important}
[style*="#d64545"]{color:var(--danger)!important;fill:var(--danger)!important;stroke:var(--danger)!important}
svg [fill="#1b5fd9"]{fill:var(--primary)!important}
svg [stroke="#1b5fd9"]{stroke:var(--primary)!important}
svg [stroke="#e3e8ef"]{stroke:var(--line)!important}
svg [stroke="#eef2f7"]{stroke:var(--line-2)!important}
"""

# ============================================================ 页面清单（P-x）
# (编号, 文件名, 页面名, 路由, 可见角色, 关键元素, 功能点, 接口, 状态覆盖)
PAGES = [
    ("P-1", "p01-login.html", "登录", "/login", "全部",
     "账号密码表单、失败提示、锁定提示", "F-1.2", "API-01",
     "默认 / 校验失败 / 账号锁定"),
    ("P-2", "p02-dashboard.html", "工作台首页", "/dashboard", "全部",
     "KPI 卡片、待办入口、最近任务", "—", "API-04 / API-93",
     "默认 / 加载 / 无权限"),
    ("P-3", "p03-system-org.html", "组织架构管理", "/system/org", "ADM / HR",
     "组织树、部门详情、新建部门", "F-1.1", "API-07~09",
     "默认 / 加载 / 空 / 错误 / 无权限"),
    ("P-4", "p04-system-user.html", "用户管理", "/system/user", "ADM",
     "用户表格、角色分配、批量导入", "F-1.1 / F-1.3", "API-13~17",
     "默认 / 加载 / 空 / 错误 / 无权限"),
    ("P-5", "p05-system-role.html", "角色与权限", "/system/role", "ADM",
     "角色列表、权限点树、数据范围", "F-1.3", "API-18~22",
     "默认 / 加载 / 错误 / 无权限"),
    ("P-6", "p06-system-audit.html", "审计日志", "/system/audit-log", "ADM / AUDIT",
     "日志表格、多条件筛选、导出", "F-1.5", "API-28 / API-29",
     "默认 / 加载 / 空 / 错误 / 无权限"),
    ("P-7", "p07-talent-list.html", "人才档案列表", "/talent/list", "ADM / HR / MGR(本部门)",
     "筛选栏、表格、标签、分页", "F-3.2", "API-30",
     "默认 / 加载 / 空 / 错误 / 无权限"),
    ("P-8", "p08-talent-detail.html", "人才档案详情", "/talent/detail/:id", "ADM / HR / MGR / EMP(本人)",
     "基础信息、履历、画像雷达图、标签、变更时间线", "F-3.2 / F-3.4 / F-3.10", "API-31 / API-34 / API-55 / API-68",
     "默认 / 加载 / 字段脱敏 / 错误 / 无权限"),
    ("P-9", "p09-talent-import.html", "批量导入与任务进度", "/talent/import", "ADM / HR",
     "拖拽上传、文件清单、步骤条、SSE 进度、质量报告", "F-3.1 / F-3.8", "API-36~43",
     "默认 / 上传中 / 解析中 / 失败重试 / 无权限"),
    ("P-10", "p10-talent-review.html", "字段复核工作台", "/talent/review", "ADM / HR",
     "复核队列、原文对照、批量确认、工作量看板", "F-3.3 / F-3.3.1", "API-44~49",
     "默认 / 加载 / 空 / 超量提示 / 无权限"),
    ("P-11", "p11-talent-dedup.html", "重复档案合并", "/talent/dedup", "ADM / HR",
     "候选对列表、并排对比、合并确认、回滚", "F-3.5 / F-3.10", "API-57~61",
     "默认 / 加载 / 空 / 冲突 / 无权限"),
    ("P-12", "p12-talent-search.html", "语义人才检索", "/talent/search", "ADM / HR / MGR",
     "自然语言输入、命中理由、引用片段、权限范围提示", "F-3.6", "API-62",
     "默认 / 加载 / 无结果 / 降级 / 越权"),
    ("P-13", "p13-tag-dict.html", "标签字典管理", "/talent/tag-dict", "ADM / HR",
     "三级分类树、标签表格、AI 建议审核", "F-3.4.1", "API-50~54",
     "默认 / 加载 / 空 / 冲突 / 无权限"),
    ("P-14", "p14-talent-ask.html", "档案 RAG 问答", "/talent/ask", "ADM / HR / MGR / EMP(本人)",
     "范围选择、对话流、引用卡片、脱敏标识、兜底提示", "F-3.7 / F-3.9", "API-63",
     "默认 / 生成中 / 兜底 / 脱敏拦截 / 无权限"),
    ("P-15", "p15-my-profile.html", "我的画像（个人权利入口）", "/my/profile", "EMP(本人)",
     "本人档案、画像、来源标注、更正/删除/导出申请", "F-3.4 / F-3.11", "API-34 / API-71~79",
     "默认 / 加载 / 申请中 / 错误"),
    ("P-16", "p16-ai-model.html", "模型配置与 Prompt 管理", "/ai/model /ai/prompt", "AIOPS",
     "模型路由表、主备切换、Prompt 版本对比", "F-9.1 / F-9.2", "API-81~87",
     "默认 / 加载 / 熔断告警 / 无权限"),
    ("P-17", "p17-ai-calllog.html", "AI 调用日志与成本看板", "/ai/call-log", "ADM / AIOPS",
     "成本 KPI、趋势图、调用明细、脱敏合规标记", "F-9.5", "API-88 / API-89",
     "默认 / 加载 / 空 / 错误 / 无权限"),
    ("P-18", "p18-agent-task.html", "Agent 任务与人工介入待办", "/ai/agent-task", "ADM / HR / AIOPS",
     "任务列表、步骤回放、待办处理、状态机", "F-A0.1~A0.5", "API-90~95",
     "默认 / 加载 / 空 / 冲突 / 无权限"),
    ("P-19", "p19-system-config.html", "数据字典与系统配置", "/system/config", "ADM",
     "字典类型、字典项维护、变更留痕", "F-1.7", "API-23~27",
     "默认 / 加载 / 空 / 冲突 / 无权限"),
]

BY_FILE = {p[1]: p for p in PAGES}

# ============================================================ 导航（分组）
NAV = [
    ("", [
        ("p02-dashboard.html", "◧", "工作台首页", ""),
    ]),
    ("人才档案", [
        ("p07-talent-list.html", "▤", "人才档案列表", ""),
        ("p12-talent-search.html", "◎", "语义人才检索", ""),
        ("p14-talent-ask.html", "✦", "档案 RAG 问答", ""),
        ("p09-talent-import.html", "⇪", "批量导入", ""),
        ("p10-talent-review.html", "✎", "字段复核工作台", ""),
        ("p11-talent-dedup.html", "⧉", "重复档案合并", ""),
        ("p13-tag-dict.html", "☰", "标签字典", ""),
        ("p15-my-profile.html", "◔", "我的画像", "本人"),
    ]),
    ("AI 中台", [
        ("p16-ai-model.html", "⚙", "模型与 Prompt", "AIOPS"),
        ("p17-ai-calllog.html", "▦", "调用日志与成本", "ADM/AIOPS"),
        ("p18-agent-task.html", "⚑", "Agent 任务与待办", ""),
    ]),
    ("系统管理", [
        ("p03-system-org.html", "⌂", "组织架构", "ADM/HR"),
        ("p04-system-user.html", "☺", "用户管理", "ADM"),
        ("p05-system-role.html", "⛨", "角色与权限", "ADM"),
        ("p19-system-config.html", "≡", "数据字典与配置", "ADM"),
        ("p06-system-audit.html", "◫", "审计日志", "ADM/AUDIT"),
    ]),
]

SIDEBAR_USER = ("张敏", "HR 管理员 · 人力资源部")


def nav_html(active):
    active_fallback = {
        'p08-talent-detail.html': 'p07-talent-list.html',
    }
    active_nav = active_fallback.get(active, active)
    out = ['<aside class="sidebar">',
           '<div class="brand"><span class="dot">才</span>'
           '<span class="brand-copy"><b>AI 人才平台</b><small>一期原型</small></span>'
           '<button class="sidebar-toggle" type="button" aria-label="收起导航" '
           'title="收起导航" onclick="document.body.classList.toggle(\'sidebar-collapsed\')">‹</button></div>',
           '<nav class="nav" aria-label="主导航">']
    for grp, items in NAV:
        if grp:
            out.append('<div class="grp">%s</div>' % grp)
        for href, ico, label, lock in items:
            cls = ' class="active"' if href == active_nav else ''
            current = ' aria-current="page"' if href == active_nav else ''
            lk = '<span class="lock">🔒 %s</span>' % lock if lock else ''
            out.append('<a href="%s"%s%s><span class="ico" aria-hidden="true">%s</span>'
                       '<span class="lbl">%s</span>%s</a>' % (href, cls, current, ico, label, lk))
    nm, role = SIDEBAR_USER
    out.append('</nav>')
    out.append('<div class="userbox"><span class="avatar">%s</span>'
               '<span class="user-meta"><b>%s</b><small>%s</small></span></div>'
               % (nm[0], nm, role))
    out.append('</aside>')
    return '\n'.join(out)


def shell(active, pno, title, crumb, meta, content, extra_css=''):
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s · %s</title>
<style>%s%s</style>
</head>
<body>
<a class="skip-link" href="#main-content">跳到主要内容</a>
<div class="app">
%s
  <button class="nav-backdrop" type="button" aria-label="关闭导航" onclick="document.body.classList.remove('nav-open')"></button>
  <main class="main">
    <div class="topbar">
      <button class="mobile-nav-toggle" type="button" aria-label="打开导航" title="打开导航" onclick="document.body.classList.toggle('nav-open')">☰</button>
      <div class="crumb"><b>%s</b>%s</div>
      <div class="acts">
        <span class="pbadge">原型 %s</span>
        <span>数据域：全部</span><span class="muted">|</span><span>张敏（HR）</span>
        <span class="muted">|</span><a href="p01-login.html">退出</a>
      </div>
    </div>
    <div class="content" id="main-content" tabindex="-1">
%s
    </div>
  </main>
</div>
</body>
</html>""" % (title, pno, CSS, extra_css, nav_html(active), title, crumb, pno, content)


# ============================================================ 状态块助手
def sbox(label, inner):
    return '<div class="state-box"><header>&lt;!-- STATE: %s --&gt;</header><div class="body">%s</div></div>' % (
        label, inner)


def s_loading(rows=4):
    return ''.join('<div class="skeleton" style="width:%d%%"></div>' % w
                   for w in ([34, 82, 68, 74, 52][:rows]))


def s_empty(title, desc, btn=''):
    return ('<div class="state-wrap"><div class="big">◌</div><h4>%s</h4><p>%s</p>%s</div>'
            % (title, desc, btn))


def s_error(title, code, trace):
    return ('<div class="state-wrap"><div class="big">!</div><h4>%s</h4>'
            '<p>错误码 %s　trace_id: %s</p><button class="btn">重新加载</button></div>'
            % (title, code, trace))


def s_forbidden(role, desc):
    return ('<div class="state-wrap"><div class="big">🔒</div><h4>无访问权限</h4>'
            '<p>当前角色「%s」%s</p><button class="btn">返回工作台</button></div>' % (role, desc))


def legend(items):
    return '<div class="legend">%s</div>' % '　·　'.join(items)


def states(*blocks):
    return '\n'.join(blocks)


# ============================================================ P-1 登录
def p01():
    box = """
<div class="login-box">
  <div class="lg"><span class="dot">才</span>
    <div><h1>AI 数字化人才平台</h1><div class="pjt">2026 顶配版 · 一期（档案与数字画像）</div></div>
  </div>
  <div class="errline" style="display:block">
    账号或密码错误，剩余可尝试 3 次<code>（code 40102）</code>
  </div>
  <div class="fi"><label>登录账号 <span style="color:#d64545">*</span></label>
    <input type="text" value="hr_zhang" placeholder="请输入账号"></div>
  <div class="fi"><label>密码 <span style="color:#d64545">*</span></label>
    <input type="password" value="············" placeholder="请输入密码"></div>
  <button class="btn btn-primary btn-login" onclick="location.href='p02-dashboard.html'">登 录<span class="mark">①</span></button>
  <div class="login-foot">
    连续 5 次失败将锁定 10 分钟（F-1.2）<br>
    支持对接甲方 SSO（OIDC / CAS 适配层预留，一期不实现）<br>
    密码 bcrypt 存储 · 传输依赖 HTTPS
  </div>
</div>"""
    side = """
<div style="width:430px">
  <div class="card">
    <div class="card-hd"><h3>状态覆盖<span class="sub">评审时逐个核对</span></h3></div>
    <div class="card-bd" style="padding:0">
      <div class="state-box" style="margin:0;border:0;border-radius:0">
        <header>&lt;!-- STATE: default — 正常登录 --&gt;</header>
        <div class="body" style="font-size:12.5px;color:#5b6673">
          账号密码均正确 → 签发 access_token + refresh_token，写入用户与数据域后跳工作台。
        </div>
      </div>
      <div class="state-box" style="margin:0;border:0;border-radius:0;border-top:1px solid #e3e8ef">
        <header>&lt;!-- STATE: invalid — 校验失败（code 40102）--&gt;</header>
        <div class="body">
          <div class="alert alert-danger" style="margin:0"><span class="i">!</span>
            <div>账号或密码错误，剩余可尝试 3 次</div></div>
        </div>
      </div>
      <div class="state-box" style="margin:0;border:0;border-radius:0;border-top:1px solid #e3e8ef">
        <header>&lt;!-- STATE: locked — 账号锁定（code 40103）--&gt;</header>
        <div class="body">
          <div class="alert alert-warn" style="margin:0"><span class="i">⏻</span>
            <div>账号已锁定，请 412 秒后重试<code>（data.locked_seconds）</code></div></div>
        </div>
      </div>
      <div class="state-box" style="margin:0;border:0;border-radius:0;border-top:1px solid #e3e8ef">
        <header>&lt;!-- STATE: forbidden — 账号被禁用（code 40301）--&gt;</header>
        <div class="body">
          <div class="alert alert-danger" style="margin:0"><span class="i">🔒</span>
            <div>账号已停用，请联系管理员（ADM）</div></div>
        </div>
      </div>
    </div>
  </div>
  <div class="legend" style="border-top:none">
    <b>对应：</b>F-1.2 · API-01 · TBL-02　<b>跳转：</b>登录成功 → /dashboard（p02-dashboard.html）<br>
    <b>不做：</b>验证码（需求未提）· SSO（预留适配层）
  </div>
</div>"""
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录 · P-1</title>
<style>%s</style>
</head>
<body>
<div class="login-wrap">
  <div style="display:flex;gap:22px;align-items:flex-start">
%s
%s
  </div>
</div>
</body>
</html>""" % (CSS, box, side)
    return html


# ============================================================ P-2 工作台首页
def p02():
    body = """
<div class="alert alert-info"><span class="i">◈</span>
  <div>您当前数据域为「<b>全部</b>」（HR · data_scope=1）。如需查看统计口径说明，见需求文档 §6.2。
  <a href="p07-talent-list.html">进入人才档案列表 →</a></div></div>

<div class="grid g4" style="margin-bottom:14px">
  <div class="stat"><div class="lbl">在库人才档案</div><div class="num">38,204<small>人</small></div>
    <div class="sub">本月新增 412 · 设计容量 10 万</div></div>
  <div class="stat"><div class="lbl">待复核字段<span class="mark">①</span></div><div class="num up">183<small>条</small></div>
    <div class="sub">高影响面 61 · 低影响面抽样 12</div></div>
  <div class="stat"><div class="lbl">待处理重复档案</div><div class="num">7<small>对</small></div>
    <div class="sub">最高相似度 0.94</div></div>
  <div class="stat"><div class="lbl">今日 AI 调用成本</div><div class="num">¥ 3.28</div>
    <div class="sub">1,743 次 · 缓存命中 11.4%</div></div>
</div>

<div class="grid g3" style="align-items:start">
  <div style="grid-column:span 2">
    <div class="card">
      <div class="card-hd"><h3>人工介入待办<span class="sub">F-A0.3 · API-93</span></h3>
        <a class="btn btn-sm" href="p18-agent-task.html">全部待办</a></div>
      <table>
        <thead><tr><th>待办类型</th><th>关联对象</th><th>触发原因</th><th>等待时长</th><th>处理人</th><th>操作</th></tr></thead>
        <tbody>
          <tr><td><span class="tag tag-warn">档案合并</span></td>
            <td>李伟 / 李伟（重复）</td><td>相似度 0.94 ≥ 阈值 0.90</td>
            <td class="muted">1 小时 12 分</td><td>张敏</td>
            <td class="ops"><a href="p11-talent-dedup.html">去处理<span class="mark">②</span></a></td></tr>
          <tr><td><span class="tag tag-info">字段复核</span></td>
            <td>IMP20260921001 · 工作年限</td><td>置信度 0.72 &lt; 0.85</td>
            <td class="muted">36 分钟</td><td>张敏</td>
            <td class="ops"><a href="p10-talent-review.html">去处理</a></td></tr>
          <tr><td><span class="tag tag-gray">高成本查询</span></td>
            <td>批量问答 · 42 份档案</td><td>预估成本 &gt; ¥ 2.00</td>
            <td class="muted">8 分钟</td><td>—</td>
            <td class="ops"><a href="p14-talent-ask.html">去确认</a></td></tr>
        </tbody>
      </table>
    </div>

    <div class="card">
      <div class="card-hd"><h3>最近导入任务<span class="sub">F-3.1 / F-3.8</span></h3>
        <a class="btn btn-sm btn-primary" href="p09-talent-import.html">+ 新建导入</a></div>
      <table>
        <thead><tr><th>任务编号</th><th>总数</th><th>成功</th><th>失败</th><th>重复</th><th>状态</th><th>完成时间</th></tr></thead>
        <tbody>
          <tr><td class="mono">IMP20260921001</td><td>100</td><td>96</td><td>4</td><td>7</td>
            <td><span class="tag tag-ok">已完成</span></td><td class="muted">09-21 14:22</td></tr>
          <tr><td class="mono">IMP20260918003</td><td>60</td><td>58</td><td>2</td><td>3</td>
            <td><span class="tag tag-warn">部分失败</span></td><td class="muted">09-18 11:07</td></tr>
          <tr><td class="mono">IMP20260921002</td><td>48</td><td>—</td><td>—</td><td>—</td>
            <td><span class="tag tag-info">解析中 62%</span></td><td class="muted">进行中</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <div>
    <div class="card">
      <div class="card-hd"><h3>AI 服务健康<span class="sub">F-9.1 / F-1.6</span></h3></div>
      <div class="card-bd">
        <table style="font-size:12.5px">
          <tbody>
            <tr><td>本地 Embedding<br><span class="muted mono">bge-m3</span></td>
              <td class="num"><span class="tag tag-ok">正常</span></td><td class="num muted">38ms</td></tr>
            <tr><td>Reranker<br><span class="muted mono">bge-reranker-v2-m3</span></td>
              <td class="num"><span class="tag tag-ok">正常</span></td><td class="num muted">96ms</td></tr>
            <tr><td>云端生成模型<br><span class="muted mono">cloud-llm-a</span></td>
              <td class="num"><span class="tag tag-warn">备用中</span></td><td class="num muted">2.1s</td></tr>
            <tr><td>Milvus<br><span class="muted mono">talent_vec</span></td>
              <td class="num"><span class="tag tag-ok">正常</span></td><td class="num muted">24ms</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="card-hd"><h3>一期范围提示</h3></div>
      <div class="card-bd" style="font-size:12.5px;color:#5b6673;line-height:1.9">
        一期仅交付 <b>F-3 档案与数字画像</b> 模块 + 配套基座（F-1 / F-9.1·9.2·9.5 / F-A0）。<br>
        以下功能域为 <b>⏸ 后续阶段</b>，本原型不含页面：<br>
        企业知识库问答（F-2）· 智能测评（F-4）· NL2SQL 问数（F-5）· 岗位匹配（F-6）· 培训（F-7）· Skills 编排（F-8）· 多端（F-10）
      </div>
    </div>
  </div>
</div>
"""
    st = states(
        sbox('loading — 骨架屏，KPI 与表格占位，操作按钮禁用', s_loading(5)),
        sbox('error — 看板聚合接口失败（code 50000）', s_error('工作台数据加载失败', '50000', 'a71b3e9c0f22')),
        sbox('forbidden — 后续模块入口（EXEC 仅统计值角色示例）',
             s_forbidden('高层管理者', '仅可见统计值，不可查看人才档案明细与导入任务')),
    )
    leg = legend([
        '<b>对应：</b>API-04 / API-93 · 跳转：待办 → p18-agent-task.html、导入 → p09-talent-import.html',
        '<b>状态：</b>默认 / 加载 / 错误 / 无权限',
        '<b>注意：</b>EXEC（data_scope=4）只看统计值，KPI 可看但明细表需隐藏',
    ])
    return shell('p02-dashboard.html', 'P-2', '工作台首页', '/ 工作台首页',
                 'F-1 / F-3 / F-A0', body + st + leg)


# ============================================================ P-3 组织架构管理
def p03():
    tree_css = """
.tree{font-size:13.5px;padding:6px 4px}
.tree .nd{display:flex;align-items:center;gap:6px;padding:6px 8px;border-radius:5px;cursor:pointer;
  color:#5b6673;white-space:nowrap}
.tree .nd:hover{background:#eef2f7}
.tree .nd.on{background:#eaf1fd;color:#1b5fd9;font-weight:600}
.tree .nd .cw{width:12px;color:#98a2ae;font-size:11px;flex:0 0 auto}
.tree .nd .cnt{margin-left:auto;font-size:11.5px;color:#98a2ae}
.tree .lv2{padding-left:20px} .tree .lv3{padding-left:40px} .tree .lv4{padding-left:60px}
"""
    body = """
<div class="toolbar">
  <button class="btn">⇱ 展开全部</button>
  <button class="btn">⇲ 折叠全部</button>
  <span class="grow"></span>
  <button class="btn">导出组织表</button>
  <button class="btn btn-primary">+ 新建部门<span class="mark">①</span></button>
</div>

<div class="grid" style="grid-template-columns:268px minmax(0,1fr);align-items:start">
  <div class="card" style="margin-bottom:0">
    <div class="card-hd"><h3>组织架构<span class="sub">TBL-07</span></h3></div>
    <div class="tree">
      <div class="nd"><span class="cw">▾</span>◈ 某某集团有限公司<span class="cnt">382</span></div>
      <div class="nd lv2"><span class="cw">▾</span>◈ 集团总部<span class="cnt">126</span></div>
      <div class="nd lv3 on"><span class="cw">▾</span>◈ 人力资源部<span class="cnt">18</span></div>
      <div class="nd lv4"><span class="cw">　</span>◌ 招聘组<span class="cnt">7</span></div>
      <div class="nd lv4"><span class="cw">　</span>◌ 人才发展组<span class="cnt">11</span></div>
      <div class="nd lv3"><span class="cw">▸</span>◈ 数据治理部<span class="cnt">34</span></div>
      <div class="nd lv3"><span class="cw">▸</span>◈ 技术研发中心<span class="cnt">74</span></div>
      <div class="nd lv2"><span class="cw">▸</span>◈ 华东分公司<span class="cnt">142</span></div>
      <div class="nd lv2"><span class="cw">▸</span>◈ 华南分公司<span class="cnt">114</span></div>
    </div>
    <div class="legend" style="border-top:1px solid #e3e8ef;padding:10px 13px;margin:0">
      <b>层级：</b>当前 4 级（需求要求支持 ≥5 级）<br>
      <b>行级权限：</b>MGR 只能看到本部门子树
    </div>
  </div>

  <div>
    <div class="card">
      <div class="card-hd"><h3>人力资源部<span class="sub">dept_code: HR</span></h3>
        <div><button class="btn btn-sm">编辑</button>
        <button class="btn btn-sm btn-danger">删除</button></div></div>
      <div class="card-bd">
        <div class="grid g4" style="gap:10px">
          <div><div class="muted" style="font-size:12px">部门编码</div><div class="mono">HR</div></div>
          <div><div class="muted" style="font-size:12px">层级</div><div>3 级</div></div>
          <div><div class="muted" style="font-size:12px">负责人</div><div>张敏</div></div>
          <div><div class="muted" style="font-size:12px">在编人数</div><div>18 人</div></div>
          <div><div class="muted" style="font-size:12px">组织路径</div><div class="mono" style="font-size:12px">集团总部 / 人力资源部</div></div>
          <div><div class="muted" style="font-size:12px">创建时间</div><div class="muted">2026-03-02 10:11</div></div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-hd"><h3>下级部门</h3><span class="sub">共 2 个</span></div>
      <table>
        <thead><tr><th>部门名称</th><th>部门编码</th><th>负责人</th><th>人数</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
          <tr><td>招聘组</td><td class="mono">HR-REC</td><td>王强</td><td>7</td>
            <td><span class="tag tag-ok">启用</span></td>
            <td class="ops"><a href="#">编辑</a><a href="#">删除</a></td></tr>
          <tr><td>人才发展组</td><td class="mono">HR-TD</td><td>赵敏</td><td>11</td>
            <td><span class="tag tag-ok">启用</span></td>
            <td class="ops"><a href="#">编辑</a><a href="#">删除</a></td></tr>
        </tbody>
      </table>
    </div>

    <div class="card">
      <div class="card-hd"><h3>部门在编人员</h3><span class="sub">展示 3 / 18</span></div>
      <table>
        <thead><tr><th>姓名</th><th>账号</th><th>岗位</th><th>角色</th><th>最后登录</th><th>操作</th></tr></thead>
        <tbody>
          <tr><td>张敏</td><td class="mono">hr_zhang</td><td>HR 负责人</td>
            <td><span class="tag tag-info">HR</span></td><td class="muted">今天 09:12</td>
            <td class="ops"><a href="p04-system-user.html">查看</a></td></tr>
          <tr><td>王强</td><td class="mono">hr_wang</td><td>招聘主管</td>
            <td><span class="tag tag-info">HR</span></td><td class="muted">今天 08:47</td>
            <td class="ops"><a href="p04-system-user.html">查看</a></td></tr>
          <tr><td>赵敏</td><td class="mono">hr_zhao</td><td>人才发展专员</td>
            <td><span class="tag tag-gray">MGR</span></td><td class="muted">昨天 17:30</td>
            <td class="ops"><a href="p04-system-user.html">查看</a></td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>
"""
    st = states(
        sbox('loading — 组织树与表格骨架屏', s_loading(5)),
        sbox('empty — 新租户无组织数据，引导新建根部门',
             s_empty('还没有组织架构', '先创建根部门（公司/单位），再逐级建立下属部门',
                     '<button class="btn btn-primary">+ 新建根部门</button>')),
        sbox('error — 组织树加载失败（code 50001）', s_error('组织架构加载失败', '50001', 'b02c7d1e5a33')),
        sbox('conflict — 删除有在职人员的部门（code 40904）',
             '<div class="alert alert-danger" style="margin:0"><span class="i">!</span>'
             '<div><b>无法删除：该部门下仍有 18 名在编人员</b><br>'
             '请先将人员调整至其他部门，或选择「停用」而非删除。<code>（code 40904 / data.dept_user_count=18）</code></div></div>'),
        sbox('forbidden — MGR / AIOPS 无组织维护权限', s_forbidden('业务经理', '仅可查看本部门信息，不可维护组织架构')),
    )
    leg = legend([
        '<b>对应：</b>F-1.1 · API-07~09 · TBL-07 / TBL-02　<b>跳转：</b>人员「查看」→ p04-system-user.html',
        '<b>状态：</b>默认 / 加载 / 空 / 错误 / 冲突 / 无权限',
        '<b>约束：</b>组织变更后 5s 内权限生效（F-1.1 验收）；删除有人员部门返回 40904 而非静默成功',
    ])
    return shell('p03-system-org.html', 'P-3', '组织架构管理', '/ 系统管理 / 组织架构',
                 'F-1.1', body + st + leg, extra_css=tree_css)


# ============================================================ P-4 用户管理
def p04():
    drawer_css = """
.drawer{border:1px solid var(--line);border-radius:var(--radius);background:#fff;overflow:hidden;
  box-shadow:0 6px 22px rgba(16,30,54,.09)}
.drawer>header{padding:11px 14px;border-bottom:1px solid var(--line);background:#fcfdff;
  display:flex;justify-content:space-between;align-items:center;font-size:13.5px;font-weight:600}
"""
    body = """
<div class="toolbar">
  <input type="text" placeholder="搜索账号 / 姓名">
  <select><option>全部部门</option><option selected>人力资源部</option><option>数据治理部</option></select>
  <select><option>全部角色</option><option>ADM</option><option selected>HR</option><option>MGR</option><option>EMP</option></select>
  <select><option>全部状态</option><option>启用</option><option>禁用</option></select>
  <button class="btn">查询</button>
  <span class="grow"></span>
  <button class="btn">⇪ 批量导入<span class="mark">①</span></button>
  <button class="btn">导出</button>
  <button class="btn btn-primary">+ 新建用户</button>
</div>

<div class="grid" style="grid-template-columns:minmax(0,1fr) 372px;align-items:start">
  <div class="card" style="margin-bottom:0">
    <table>
      <thead><tr><th>账号</th><th>姓名</th><th>部门</th><th>角色</th><th>数据范围</th>
        <th>状态</th><th>最后登录</th><th>操作</th></tr></thead>
      <tbody>
        <tr><td class="mono">hr_zhang</td><td>张敏</td><td>人力资源部</td>
          <td><span class="tag tag-info">HR</span></td><td class="muted">全部</td>
          <td><span class="tag tag-ok">启用</span></td><td class="muted">今天 09:12</td>
          <td class="ops"><a href="#">角色</a><a href="#">编辑</a><a href="#">禁用</a></td></tr>
        <tr><td class="mono">hr_wang</td><td>王强</td><td>人力资源部 / 招聘组</td>
          <td><span class="tag tag-info">HR</span></td><td class="muted">全部</td>
          <td><span class="tag tag-ok">启用</span></td><td class="muted">今天 08:47</td>
          <td class="ops"><a href="#">角色</a><a href="#">编辑</a><a href="#">禁用</a></td></tr>
        <tr><td class="mono">mgr_li</td><td>李伟</td><td>数据治理部</td>
          <td><span class="tag tag-gray">MGR</span></td><td class="muted">本部门</td>
          <td><span class="tag tag-ok">启用</span></td><td class="muted">昨天 18:02</td>
          <td class="ops"><a href="#">角色</a><a href="#">编辑</a><a href="#">禁用</a></td></tr>
        <tr><td class="mono">exec_chen</td><td>陈立</td><td>集团总部</td>
          <td><span class="tag tag-warn">EXEC</span></td><td class="muted">仅统计值</td>
          <td><span class="tag tag-ok">启用</span></td><td class="muted">09-19 15:41</td>
          <td class="ops"><a href="#">角色</a><a href="#">编辑</a><a href="#">禁用</a></td></tr>
        <tr><td class="mono">audit_zhou</td><td>周航</td><td>审计部</td>
          <td><span class="tag tag-gray">AUDIT</span></td><td class="muted">仅统计值</td>
          <td><span class="tag tag-ok">启用</span></td><td class="muted">09-18 10:07</td>
          <td class="ops"><a href="#">角色</a><a href="#">编辑</a><a href="#">禁用</a></td></tr>
        <tr><td class="mono">emp_zhao</td><td>赵敏</td><td>数据治理部</td>
          <td><span class="tag tag-line">EMP</span></td><td class="muted">仅本人</td>
          <td><span class="tag tag-danger">禁用</span></td><td class="muted">08-30 09:05</td>
          <td class="ops"><a href="#">角色</a><a href="#">编辑</a><a href="#">启用</a></td></tr>
      </tbody>
    </table>
    <div class="pager"><span>共 62 条　每页 20 条</span>
      <div class="pages"><span>‹</span><span class="cur">1</span><span>2</span><span>3</span><span>4</span><span>›</span></div></div>
  </div>

  <div class="drawer">
    <header><span>分配角色 · 张敏</span><span class="muted" style="font-weight:400;font-size:12px">API-16</span></header>
    <div class="card-bd">
      <div style="font-size:13px;color:#5b6673;margin-bottom:10px">选择角色（可多选）<span class="mark">②</span></div>
      <label class="check" style="display:flex;margin-bottom:9px">
        <input type="checkbox" checked> <span><b>HR</b> 管理员<br>
        <span class="muted" style="font-size:12px">全量业务数据 · 档案 / 复核 / 标签 / 问答</span></span></label>
      <label class="check" style="display:flex;margin-bottom:9px">
        <input type="checkbox"> <span><b>ADM</b> 超级管理员<br>
        <span class="muted" style="font-size:12px">系统配置与权限分配（不含业务数据明文导出）</span></span></label>
      <label class="check" style="display:flex;margin-bottom:9px">
        <input type="checkbox"> <span><b>AUDIT</b> 审计员<br>
        <span class="muted" style="font-size:12px">只读审计日志与变更留痕</span></span></label>
      <label class="check" style="display:flex;margin-bottom:14px">
        <input type="checkbox"> <span><b>EMP</b> 普通员工<br>
        <span class="muted" style="font-size:12px">仅本人画像与个人权利响应</span></span></label>
      <div class="alert alert-info" style="margin-bottom:14px"><span class="i">◈</span>
        <div>角色决定「数据范围」（data_scope）：全部 / 本部门 / 本人 / 仅统计值。<br>
        权限变更写审计日志，5s 内生效。</div></div>
      <button class="btn btn-primary" style="width:100%;justify-content:center">保存</button>
    </div>
  </div>
</div>
"""
    st = states(
        sbox('loading — 表格骨架屏，导入按钮禁用', s_loading(5)),
        sbox('empty — 无用户，引导新建或导入',
             s_empty('还没有用户', '可单个新建，或用 Excel 模板批量导入（支持 ≥5 级部门）',
                     '<button class="btn btn-primary">+ 新建用户</button> <button class="btn">⇪ 批量导入</button>')),
        sbox('error — 用户列表加载失败（code 50001）', s_error('用户列表加载失败', '50001', 'c13d8e2f6b44')),
        sbox('conflict — 账号重复（code 40901）',
             '<div class="alert alert-danger" style="margin:0"><span class="i">!</span>'
             '<div><b>账号 hr_zhang 已存在</b>　请更换账号，或先停用原账号'
             '<code>（code 40901 / data.existing_user_id）</code></div></div>'),
        sbox('forbidden — HR / MGR 无用户管理权限', s_forbidden('HR 管理员', '仅可维护组织架构，用户与权限管理需 ADM')),
    )
    leg = legend([
        '<b>对应：</b>F-1.1 / F-1.3 · API-13~17 · TBL-02 / TBL-05',
        '<b>跳转：</b>角色 → p05-system-role.html、部门 → p03-system-org.html',
        '<b>状态：</b>默认 / 加载 / 空 / 错误 / 冲突 / 无权限',
        '<b>注意：</b>「批量导入」走 Excel 模板（API-17），与人才档案导入（p09）不是同一入口',
    ])
    return shell('p04-system-user.html', 'P-4', '用户管理', '/ 系统管理 / 用户管理',
                 'F-1.1 / F-1.3', body + st + leg, extra_css=drawer_css)


# ============================================================ P-5 角色与权限
def p05():
    tree_css = """
.tree{font-size:13.5px;padding:4px 2px}
.nd{display:flex;align-items:center;gap:6px;padding:6px 8px;border-radius:5px;color:#5b6673}
.nd:hover{background:#eef2f7}
.nd .cw{width:12px;color:#98a2ae;font-size:11px}
.nd .rw{margin-left:auto;font-size:11.5px}
.lv2{padding-left:22px} .lv3{padding-left:44px}
"""
    body = """
<div class="alert alert-warn"><span class="i">◈</span>
  <div><b>权限矩阵来自需求文档 §3.2，本页只能配置、不能超越。</b>
  7 个预置角色的行级/字段级权限生效率达 100%，并提供 ≥30 条越权测试用例全部拦截（F-1.3 验收）。</div></div>

<div class="grid" style="grid-template-columns:300px minmax(0,1fr);align-items:start">
  <div class="card" style="margin-bottom:0">
    <div class="card-hd"><h3>角色列表<span class="sub">TBL-03</span></h3>
      <button class="btn btn-sm btn-primary">+ 新建</button></div>
    <table style="font-size:13px">
      <thead><tr><th>角色</th><th>数据范围</th></tr></thead>
      <tbody>
        <tr style="background:#eaf1fd"><td><b style="color:#1b5fd9">ADM</b> 超级管理员</td><td class="muted">全部</td></tr>
        <tr><td>EXEC 高层管理者</td><td class="muted">仅统计值</td></tr>
        <tr><td>HR 管理员</td><td class="muted">全部</td></tr>
        <tr><td>MGR 业务经理</td><td class="muted">本部门</td></tr>
        <tr><td>EMP 普通员工</td><td class="muted">本人</td></tr>
        <tr><td>AIOPS AI 运维</td><td class="muted">仅统计值</td></tr>
        <tr><td>AUDIT 审计员</td><td class="muted">仅统计值</td></tr>
        <tr><td>RECRUITER <span class="tag tag-line">自定义</span></td><td class="muted">本部门</td></tr>
      </tbody>
    </table>
    <div class="legend" style="padding:10px 13px;margin:0;border-top:1px solid #e3e8ef">
      <b>预置 7 个</b>不可删除；自定义角色可停用。<br>用户数 &gt; 0 的角色不可删除（40904）。
    </div>
  </div>

  <div>
    <div class="card">
      <div class="card-hd"><h3>ADM · 超级管理员<span class="sub">role_code: ADM</span></h3>
        <div><button class="btn btn-sm">重命名</button>
        <button class="btn btn-sm" disabled>删除</button></div></div>
      <div class="card-bd">
        <div class="grid g3" style="gap:12px">
          <div><div class="muted" style="font-size:12px">数据范围</div>
            <select style="width:100%;margin-top:4px"><option selected>全部</option><option>本部门</option><option>本人</option><option>仅统计值</option></select>
            <div class="form-help">决定行级 + 字段级裁剪（§1.7）</div></div>
          <div><div class="muted" style="font-size:12px">绑定用户数</div><div style="font-size:19px;font-weight:700">2</div>
            <div class="form-help">停用需先解除用户绑定</div></div>
          <div><div class="muted" style="font-size:12px">权限点数</div><div style="font-size:19px;font-weight:700">96</div>
            <div class="form-help">菜单 12 / 按钮 54 / 接口 30</div></div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-hd"><h3>权限点树<span class="sub">TBL-04 · 勾选即绑定（API-22）</span></h3>
        <div><button class="btn btn-sm">全选</button><button class="btn btn-sm">清空</button></div></div>
      <div class="tree">
        <div class="nd"><span class="cw">▾</span><input type="checkbox" checked> 人才档案管理<span class="rw muted">28 项</span></div>
        <div class="nd lv2"><span class="cw">▾</span><input type="checkbox" checked> 档案列表与详情<span class="rw muted">8 项</span></div>
        <div class="nd lv3"><span class="cw">　</span><input type="checkbox" checked> 查看档案</div>
        <div class="nd lv3"><span class="cw">　</span><input type="checkbox" checked> 编辑档案</div>
        <div class="nd lv3"><span class="cw">　</span><input type="checkbox" checked> 导出档案</div>
        <div class="nd lv2"><span class="cw">▾</span><input type="checkbox" checked> 批量导入<span class="rw muted">6 项</span></div>
        <div class="nd lv2"><span class="cw">▾</span><input type="checkbox" checked> 字段复核<span class="rw muted">6 项</span></div>
        <div class="nd lv2"><span class="cw">▾</span><input type="checkbox" checked> 标签字典管理<span class="rw muted">5 项</span></div>
        <div class="nd lv2"><span class="cw">▾</span><input type="checkbox" checked> 重复合并</div>
        <div class="nd lv2"><span class="cw">▾</span><input type="checkbox" checked> 语义检索与问答</div>
        <div class="nd lv2"><span class="cw">▸</span><input type="checkbox" checked> 个人权利审批<span class="rw muted">5 项</span></div>
        <div class="nd"><span class="cw">▾</span><input type="checkbox" checked> 系统管理<span class="rw muted">41 项</span></div>
        <div class="nd lv2"><span class="cw">▾</span><input type="checkbox" checked> 组织架构</div>
        <div class="nd lv2"><span class="cw">▾</span><input type="checkbox" checked> 用户与角色</div>
        <div class="nd lv2"><span class="cw">▾</span><input type="checkbox" checked> 数据字典</div>
        <div class="nd lv2"><span class="cw">▸</span><input type="checkbox"> 审计日志导出<span class="rw muted">AUDIT 专属</span></div>
        <div class="nd"><span class="cw">▸</span><input type="checkbox"> AI 中台运维<span class="rw muted">AIOPS 专属</span></div>
      </div>
      <div class="pager" style="border-top:1px solid #e3e8ef">
        <span>已选 96 项</span>
        <div><button class="btn btn-sm">重置</button>
        <button class="btn btn-sm btn-primary">保存权限<span class="mark">①</span></button></div>
      </div>
    </div>
  </div>
</div>
"""
    st = states(
        sbox('loading — 角色与权限树骨架屏', s_loading(5)),
        sbox('error — 权限点树加载失败（code 50001）', s_error('权限点加载失败', '50001', 'd24e9f3a7c55')),
        sbox('conflict — 删除仍有用户绑定的角色（code 40904）',
             '<div class="alert alert-danger" style="margin:0"><span class="i">!</span>'
             '<div><b>无法删除：RECRUITER 仍绑定 5 个用户</b><br>请先解除绑定或改用「停用」。</div></div>'),
        sbox('forbidden — 非 ADM 不可访问', s_forbidden('HR 管理员', '仅 ADM 可配置角色与权限')),
    )
    leg = legend([
        '<b>对应：</b>F-1.3 · API-18~22 · TBL-03 / TBL-04 / TBL-06',
        '<b>状态：</b>默认 / 加载 / 错误 / 冲突 / 无权限',
        '<b>红线：</b>权限只能收紧不能放开 —— 预置 7 角色覆盖 §3.2 矩阵，自定义角色不得突破租户与数据范围边界',
    ])
    return shell('p05-system-role.html', 'P-5', '角色与权限', '/ 系统管理 / 角色与权限',
                 'F-1.3', body + st + leg, extra_css=tree_css)


# ============================================================ P-6 审计日志
def p06():
    body = """
<div class="grid g4" style="margin-bottom:14px">
  <div class="stat"><div class="lbl">今日日志</div><div class="num">2,187<small>条</small></div>
    <div class="sub">日均 2,000 · 保留 ≥12 个月</div></div>
  <div class="stat"><div class="lbl">敏感操作</div><div class="num">14<small>次</small></div>
    <div class="sub">导出 / 权限变更 / 删除审批</div></div>
  <div class="stat"><div class="lbl">数据导出</div><div class="num">3<small>次</small></div>
    <div class="sub">全部已留痕</div></div>
  <div class="stat"><div class="lbl">检索行为</div><div class="num">326<small>次</small></div>
    <div class="sub">含越权尝试 2 次（已拦截）</div></div>
</div>

<div class="toolbar">
  <input type="text" placeholder="起始时间" value="2026-09-21 00:00" style="min-width:152px">
  <span class="muted">→</span>
  <input type="text" placeholder="结束时间" value="2026-09-21 23:59" style="min-width:152px">
  <select><option>全部操作类型</option><option>登录</option><option>数据导出</option>
    <option>权限变更</option><option>AI 调用</option><option>检索行为</option><option>删除审批</option></select>
  <select><option>全部用户</option><option>张敏</option><option>王强</option></select>
  <button class="btn">查询</button>
  <span class="grow"></span>
  <button class="btn">导出审计日志<span class="mark">①</span></button>
</div>

<div class="card">
  <div class="card-hd"><h3>审计日志<span class="sub">TBL-28 · 只追加，不可篡改</span></h3>
    <span class="muted" style="font-size:12px">仅 ADM / AUDIT 可见</span></div>
  <table>
    <thead><tr><th>时间</th><th>操作人</th><th>操作类型</th><th>对象</th><th>结果</th>
      <th>IP</th><th>trace_id</th></tr></thead>
    <tbody>
      <tr><td class="muted nowrap">09-21 16:20:41</td><td>张敏</td>
        <td><span class="tag tag-danger">删除审批</span></td>
        <td>个人权利请求 · 删除 · 李伟</td><td><span class="tag tag-ok">成功</span></td>
        <td class="mono">10.20.3.51</td><td class="mono muted">b24c9d3e7a66</td></tr>
      <tr><td class="muted nowrap">09-21 15:58:02</td><td>王强</td>
        <td><span class="tag tag-warn">数据导出</span></td>
        <td>人才档案导出 · 386 条</td><td><span class="tag tag-ok">成功</span></td>
        <td class="mono">10.20.3.72</td><td class="mono muted">a8f1c0d3e921</td></tr>
      <tr><td class="muted nowrap">09-21 15:41:10</td><td>陈立</td>
        <td><span class="tag tag-danger">越权拦截</span></td>
        <td>语义检索 · org_id 超出数据域</td><td><span class="tag tag-danger">已拦截 40301</span></td>
        <td class="mono">10.20.9.14</td><td class="mono muted">e91f6a0b4d33</td></tr>
      <tr><td class="muted nowrap">09-21 14:31:02</td><td>系统</td>
        <td><span class="tag tag-info">AI 调用</span></td>
        <td>档案问答 · cloud-llm-a · ¥0.0421</td><td><span class="tag tag-ok">成功</span></td>
        <td class="mono">—</td><td class="mono muted">8a1b2c3d</td></tr>
      <tr><td class="muted nowrap">09-21 14:22:07</td><td>张敏</td>
        <td><span class="tag tag-gray">批量导入</span></td>
        <td>IMP20260921001 · 100 份</td><td><span class="tag tag-ok">成功</span></td>
        <td class="mono">10.20.3.51</td><td class="mono muted">c13d8e2f6b44</td></tr>
      <tr><td class="muted nowrap">09-21 09:12:33</td><td>张敏</td>
        <td><span class="tag tag-gray">登录</span></td>
        <td>hr_zhang</td><td><span class="tag tag-ok">成功</span></td>
        <td class="mono">10.20.3.51</td><td class="mono muted">9f2a1c7e4b0d</td></tr>
      <tr><td class="muted nowrap">09-21 09:11:58</td><td>hr_zhang</td>
        <td><span class="tag tag-gray">登录</span></td>
        <td>密码错误（第 4 次）</td><td><span class="tag tag-danger">失败</span></td>
        <td class="mono">10.20.3.51</td><td class="mono muted">9f2a1c7e4b0c</td></tr>
    </tbody>
  </table>
  <div class="pager"><span>共 219,834 条　每页 20 条（按月分区，查询须带时间范围）</span>
    <div class="pages"><span>‹</span><span class="cur">1</span><span>2</span><span>3</span><span>…</span><span>10992</span><span>›</span></div></div>
</div>
"""
    st = states(
        sbox('loading — 日志表格骨架屏，导出按钮禁用', s_loading(4)),
        sbox('empty — 所选时间段无日志',
             s_empty('该时间段内没有审计记录', '请调整时间范围，或确认筛选条件是否正确')),
        sbox('error — 日志查询失败（code 50001）', s_error('审计日志加载失败', '50001', 'f46a1b5c9e77')),
        sbox('forbidden — HR / MGR / EMP / AIOPS 不可访问',
             s_forbidden('HR 管理员', '仅 ADM 与 AUDIT 可查看审计日志（AUDIT 只读，不可导出除外）')),
    )
    leg = legend([
        '<b>对应：</b>F-1.5 · API-28 / API-29 · TBL-28　<b>红线：</b>三类日志只追加，不更新不软删，按月分区',
        '<b>状态：</b>默认 / 加载 / 空 / 错误 / 无权限',
        '<b>注意：</b>「导出审计日志」自身也要写一条审计日志（API-29 备注）',
    ])
    return shell('p06-system-audit.html', 'P-6', '审计日志', '/ 系统管理 / 审计日志',
                 'F-1.5', body + st + leg)


# ============================================================ P-7 人才档案列表
def p07():
    body = """
<div class="alert alert-warn"><span class="i">◈</span>
  <div><b>本页是「结构化筛选」，不是语义检索。</b>自然语言找人才请用
  <a href="p12-talent-search.html">语义人才检索</a>（F-3.6，支持「找擅长数字化转型、3 年以上 AI 项目经验的骨干」）。</div></div>

<div class="toolbar">
  <input type="text" placeholder="姓名（模糊）">
  <select><option>全部部门</option><option selected>数据治理部</option><option>技术研发中心</option></select>
  <select><option>学历不限</option><option>大专</option><option>本科</option><option selected>硕士及以上</option></select>
  <select><option>工作年限不限</option><option>1-3 年</option><option selected>3-5 年</option><option>5 年以上</option></select>
  <select><option>全部状态</option><option selected>有效</option><option>待复核</option><option>已合并</option></select>
  <button class="btn">查询<span class="mark">①</span></button>
  <button class="btn btn-text">重置</button>
  <span class="grow"></span>
  <button class="btn">导出<span class="mark">③</span></button>
  <button class="btn">⇪ 批量导入</button>
</div>

<div style="margin-bottom:12px">
  <span class="muted" style="font-size:12.5px;margin-right:6px">已选标签：</span>
  <span class="chip">数据治理<span class="x">×</span></span>
  <span class="chip">AI 项目经验<span class="x">×</span></span>
  <a href="p13-tag-dict.html" style="font-size:12.5px">标签只能取自受控词表 →</a>
</div>

<div class="card">
  <div class="card-hd"><h3>人才档案<span class="sub">共 382 条命中</span></h3>
    <span class="muted" style="font-size:12px">数据范围：全部（HR · data_scope=1）</span></div>
  <table>
    <thead><tr><th>姓名</th><th>性别</th><th>部门</th><th>学历</th><th>工作年限</th>
      <th>标签</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead>
    <tbody>
      <tr><td><b>李伟</b></td><td>男</td><td>数据治理部</td><td>硕士</td><td class="num" style="text-align:left">6 年</td>
        <td><span class="tag tag-info">数据治理</span> <span class="tag tag-info">AI 项目经验</span></td>
        <td><span class="tag tag-ok">有效</span></td><td class="muted nowrap">09-21 14:22</td>
        <td class="ops"><a href="p08-talent-detail.html">详情<span class="mark">②</span></a><a href="p14-talent-ask.html">问答</a></td></tr>
      <tr><td><b>王强</b></td><td>男</td><td>技术研发中心</td><td>本科</td><td class="num" style="text-align:left">8 年</td>
        <td><span class="tag tag-info">数字化转型</span> <span class="tag tag-info">架构设计</span></td>
        <td><span class="tag tag-ok">有效</span></td><td class="muted nowrap">09-21 11:03</td>
        <td class="ops"><a href="p08-talent-detail.html">详情</a><a href="p14-talent-ask.html">问答</a></td></tr>
      <tr><td><b>赵敏</b></td><td>女</td><td>数据治理部</td><td>硕士</td><td class="num" style="text-align:left">4 年</td>
        <td><span class="tag tag-info">数据标准</span> <span class="tag tag-warn">待完善</span></td>
        <td><span class="tag tag-warn">待复核</span></td><td class="muted nowrap">09-21 10:40</td>
        <td class="ops"><a href="p10-talent-review.html">去复核</a></td></tr>
      <tr><td><b>陈立</b></td><td>男</td><td>集团总部</td><td>博士</td><td class="num" style="text-align:left">12 年</td>
        <td><span class="tag tag-info">战略规划</span></td>
        <td><span class="tag tag-ok">有效</span></td><td class="muted nowrap">09-19 15:41</td>
        <td class="ops"><a href="p08-talent-detail.html">详情</a></td></tr>
      <tr><td><span class="ell" title="欧阳若曦（复姓超长姓名用于验证省略号与排版）">欧阳若曦（复姓超长姓名用于验证省略号与排版）</span></td>
        <td>女</td><td>人力资源部 / 人才发展组</td><td>硕士</td><td class="num" style="text-align:left">5 年</td>
        <td><span class="tag tag-info">组织发展</span></td>
        <td><span class="tag tag-ok">有效</span></td><td class="muted nowrap">09-18 16:20</td>
        <td class="ops"><a href="p08-talent-detail.html">详情</a></td></tr>
      <tr><td><b>周航</b></td><td>男</td><td>审计部</td><td>本科</td><td class="muted" style="text-align:left">—</td>
        <td class="muted">—</td>
        <td><span class="tag tag-danger">已删除</span></td><td class="muted nowrap">09-15 09:05</td>
        <td class="ops"><a href="p15-my-profile.html">删除留痕</a></td></tr>
    </tbody>
  </table>
  <div class="pager"><span>共 382 条　每页 20 条　（per_page 上限 100）</span>
    <div class="pages"><span>‹</span><span class="cur">1</span><span>2</span><span>3</span><span>…</span><span>20</span><span>›</span></div></div>
</div>
"""
    st = states(
        sbox('loading — 表格骨架屏，导出与导入按钮禁用', s_loading(5)),
        sbox('empty — 筛选无结果 / 全库无档案',
             s_empty('没有符合条件的人才档案', '试试放宽筛选条件，或使用语义检索「找擅长数字化转型的骨干」',
                     '<button class="btn">重置筛选</button> <a class="btn btn-primary" href="p12-talent-search.html">去语义检索</a>')),
        sbox('error — 列表加载失败（code 50001）', s_error('人才档案加载失败', '50001', 'a71b3e9c0f22')),
        sbox('boundary — 边界数据表现（超长文本 / 空字段 / 已删除）',
             '<table style="border:1px solid #e3e8ef;border-radius:5px;overflow:hidden">'
             '<thead><tr><th>姓名</th><th>工作年限</th><th>标签</th><th>状态</th></tr></thead><tbody>'
             '<tr><td><span class="ell" title="欧阳若曦（复姓超长姓名用于验证省略号与排版）">欧阳若曦（复姓超长姓名用于验证省略号与排版）</span></td>'
             '<td>—</td><td class="muted">—</td><td><span class="tag tag-danger">已删除</span></td></tr>'
             '</tbody></table>'
             '<div class="form-help">空字段显示 <code>—</code> 而非空白；已删除档案仅 ADM/HR 可见；'
             '<b>列表中的 id 必须是字符串</b>（雪花 ID 精度坑），前端不得 parseInt。</div>'),
        sbox('forbidden — EMP 调用档案列表（应走「我的画像」）',
             s_forbidden('普通员工', '仅可查看本人档案，走「我的画像」入口')),
    )
    leg = legend([
        '<b>对应：</b>F-3.2 · API-30 · TBL-11 / TBL-07　<b>跳转：</b>② 详情 → p08-talent-detail.html',
        '<b>状态：</b>默认 / 加载 / 空 / 错误 / 边界 / 无权限',
        '<b>红线：</b>MGR 的行级条件由服务端自动注入（org_id 子树），前端传参只能收窄；EMP 直接 40301',
        '<b>必跑用例：</b>用列表返回的 ID 请求详情 —— 唯一能当场暴露雪花 ID 精度丢失的检查',
    ])
    return shell('p07-talent-list.html', 'P-7', '人才档案列表', '/ 人才档案 / 档案列表',
                 'F-3.2', body + st + leg)


# ============================================================ P-8 人才档案详情
RADAR = """
<div class="chart"><svg viewBox="0 0 230 210" width="230" height="210" role="img" aria-label="能力画像雷达图">
  <g fill="none" stroke="#e3e8ef" stroke-width="1">
    <polygon points="110,35 176.6,83.4 151.1,161.6 68.9,161.6 43.4,83.4"/>
    <polygon points="110,58.3 154.4,90.6 137.4,142.8 82.6,142.8 65.6,90.6"/>
    <polygon points="110,81.7 132.2,97.8 123.7,123.9 96.3,123.9 87.8,97.8"/>
  </g>
  <g stroke="#eef2f7" stroke-width="1">
    <line x1="110" y1="105" x2="110" y2="35"/><line x1="110" y1="105" x2="176.6" y2="83.4"/>
    <line x1="110" y1="105" x2="151.1" y2="161.6"/><line x1="110" y1="105" x2="68.9" y2="161.6"/>
    <line x1="110" y1="105" x2="43.4" y2="83.4"/>
  </g>
  <polygon points="110,45.5 156.6,89.9 134.7,139 73,156 60.1,88.8"
    fill="#1b5fd9" fill-opacity="0.16" stroke="#1b5fd9" stroke-width="1.6"/>
  <g fill="#1b5fd9">
    <circle cx="110" cy="45.5" r="2.6"/><circle cx="156.6" cy="89.9" r="2.6"/>
    <circle cx="134.7" cy="139" r="2.6"/><circle cx="73" cy="156" r="2.6"/>
    <circle cx="60.1" cy="88.8" r="2.6"/>
  </g>
  <g fill="#5b6673" font-size="11" font-family="-apple-system,Microsoft YaHei,sans-serif">
    <text x="110" y="24" text-anchor="middle">技能深度</text>
    <text x="184" y="78" text-anchor="middle">项目复杂度</text>
    <text x="152" y="178" text-anchor="middle">行业经验</text>
    <text x="66" y="178" text-anchor="middle">数据治理</text>
    <text x="34" y="78" text-anchor="middle">团队协作</text>
  </g>
</svg></div>"""


def p08():
    body = """
<div class="toolbar">
  <a class="btn" href="p07-talent-list.html">‹ 返回列表</a>
  <span class="grow"></span>
  <span class="tag tag-warn">身份字段已脱敏展示（MGR 视角）</span>
  <button class="btn">编辑档案<span class="mark">①</span></button>
  <button class="btn">重新解析</button>
  <button class="btn btn-danger">归档</button>
</div>

<div class="grid" style="grid-template-columns:minmax(0,1fr) 340px;align-items:start">
  <div>
    <div class="card">
      <div class="card-hd">
        <h3>李伟<span class="sub">id: 1839204770000001001（字符串）</span></h3>
        <span><span class="tag tag-ok">有效</span> <span class="tag tag-gray">version_no: 3</span></span>
      </div>
      <div class="card-bd">
        <div class="grid g4" style="gap:12px">
          <div><div class="muted" style="font-size:12px">性别</div><div>男</div></div>
          <div><div class="muted" style="font-size:12px">出生年月</div><div>1993-06</div></div>
          <div><div class="muted" style="font-size:12px">学历</div><div>硕士（04）</div></div>
          <div><div class="muted" style="font-size:12px">工作年限</div><div>6 年</div></div>
          <div><div class="muted" style="font-size:12px">院校</div><div>某某大学</div></div>
          <div><div class="muted" style="font-size:12px">专业</div><div>计算机科学与技术</div></div>
          <div><div class="muted" style="font-size:12px">部门</div><div>数据治理部</div></div>
          <div><div class="muted" style="font-size:12px">电话</div><div class="mono">138****8888 🔒</div></div>
          <div><div class="muted" style="font-size:12px">邮箱</div><div class="mono">li***@example.com 🔒</div></div>
          <div><div class="muted" style="font-size:12px">身份证</div><div class="mono">3301**********1234 🔒</div></div>
          <div><div class="muted" style="font-size:12px">更新时间</div><div class="muted">2026-09-21 14:22</div></div>
          <div><div class="muted" style="font-size:12px">数据来源</div><div class="muted">简历_李伟.pdf · AI 解析</div></div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-hd"><h3>工作经历<span class="sub">2 段</span></h3>
        <span class="muted" style="font-size:12px">来源：AI 解析（置信度 0.93）</span></div>
      <table>
        <thead><tr><th>时间</th><th>公司</th><th>职位</th><th>描述</th></tr></thead>
        <tbody>
          <tr><td class="nowrap muted">2023-09 ~ 至今</td><td>某某集团有限公司</td><td>数据治理工程师</td>
            <td><span class="ell" title="负责数据标准与主数据管理平台建设，牵头 12 个业务域的数据资产盘点">负责数据标准与主数据管理平台建设，牵头 12 个业务域的数据资产盘点</span></td></tr>
          <tr><td class="nowrap muted">2020-03 ~ 2023-08</td><td>某某科技有限公司</td><td>数据开发工程师</td>
            <td><span class="ell" title="参与数据仓库建设，负责 ETL 调度与数据质量校验规则">参与数据仓库建设，负责 ETL 调度与数据质量校验规则</span></td></tr>
        </tbody>
      </table>
    </div>

    <div class="card">
      <div class="card-hd"><h3>项目经历<span class="sub">3 个 · 其中 1 个命中检索</span></h3></div>
      <table>
        <thead><tr><th>项目</th><th>角色</th><th>时间</th><th>描述片段</th></tr></thead>
        <tbody>
          <tr><td>集团主数据平台</td><td>技术负责人</td><td class="muted nowrap">2021-01 ~ 2022-12</td>
            <td><span class="ell" title="主导集团主数据平台建设，覆盖 12 个业务域，负责数据标准与主数据管理">主导集团主数据平台建设，覆盖 12 个业务域…</span>
            <span class="tag tag-ok">检索命中</span></td></tr>
          <tr><td>数据质量监控体系</td><td>核心开发</td><td class="muted nowrap">2022-03 ~ 2022-11</td>
            <td><span class="ell" title="建设数据质量规则引擎，覆盖完整性/一致性/及时性三类校验">建设数据质量规则引擎…</span></td></tr>
          <tr><td>数据资产目录</td><td>参与</td><td class="muted nowrap">2023-02 ~ 2023-07</td>
            <td><span class="ell" title="参与数据资产目录编制，完成 800+ 数据项元数据登记">参与数据资产目录编制…</span></td></tr>
        </tbody>
      </table>
    </div>

    <div class="card">
      <div class="card-hd"><h3>技能与证书</h3></div>
      <div class="card-bd">
        <div style="margin-bottom:12px">
          <span class="chip">数据治理 · 精通</span><span class="chip">主数据管理 · 熟练</span>
          <span class="chip">数据标准 · 熟练</span><span class="chip">SQL · 精通</span>
          <span class="chip">Python · 熟练</span><span class="chip">ETL · 熟练</span>
        </div>
        <table>
          <thead><tr><th>证书名称</th><th>取得时间</th><th>来源</th></tr></thead>
          <tbody>
            <tr><td>CDGA 数据治理工程师</td><td class="muted">2021-06</td><td><span class="tag tag-info">AI 解析</span></td></tr>
            <tr><td>CDGP 数据治理专家</td><td class="muted">2024-09</td><td><span class="tag tag-info">AI 解析</span></td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="card-hd"><h3>变更留痕<span class="sub">F-3.10 · 只追加，不可篡改</span></h3>
        <a class="btn btn-sm" href="#snapshot">查看时间点快照</a></div>
      <div class="card-bd">
        <div class="timeline">
          <div class="tl-item"><div class="tl-head"><b>工作年限</b> 5 → <span style="color:#1f9254;font-weight:600">6</span>
            <span class="tag tag-warn">人工复核</span><span class="who">张敏</span>
            <span class="muted">2026-09-21 16:22</span></div>
            <div class="tl-body">来源：字段复核工作台（T-3.3）· 任务 IMP20260921001</div></div>
          <div class="tl-item ai"><div class="tl-head"><b>技能</b> 新增「CDGP 数据治理专家」
            <span class="tag tag-info">AI 解析</span><span class="who">系统</span>
            <span class="muted">2026-09-21 14:20</span></div>
            <div class="tl-body">来源：简历_李伟.pdf（第 2 页「证书」区块）· 置信度 0.91</div></div>
          <div class="tl-item merge"><div class="tl-head"><b>档案合并</b> 主档 ← 从档 1839204770000001002
            <span class="tag tag-danger">合并</span><span class="who">王强</span>
            <span class="muted">2026-08-20 16:02</span></div>
            <div class="tl-body">合并字段 2 项，迁移明细 11 条 · <b>快照 id: 1839204770000013002</b>
              （<a href="p11-talent-dedup.html">可回滚</a>）</div></div>
          <div class="tl-item ai"><div class="tl-head"><b>工作经历</b> 新增 1 段「某某集团有限公司 · 数据治理工程师」
            <span class="tag tag-info">重解析</span><span class="who">系统</span>
            <span class="muted">2026-07-14 09:31</span></div>
            <div class="tl-body">来源：重新上传的简历（version_no 2 → 3）</div></div>
          <div class="tl-item"><div class="tl-head"><b>档案创建</b>
            <span class="tag tag-gray">AI 解析</span><span class="who">张敏</span>
            <span class="muted">2026-03-02 10:11</span></div>
            <div class="tl-body">来源：简历_李伟.pdf · 首次解析字段 18 项</div></div>
        </div>
      </div>
    </div>
  </div>

  <div>
    <div class="card">
      <div class="card-hd"><h3>数字画像<span class="sub">F-3.4</span></h3>
        <button class="btn btn-sm">重算</button></div>
      <div class="card-bd" style="padding:6px 0 0">
""" + RADAR + """
        <div style="padding:0 14px 14px">
          <div class="muted" style="font-size:12px;margin-bottom:6px">画像标签（受控词表 F-3.4.1）</div>
          <span class="chip">数据治理<span class="x">×</span></span>
          <span class="chip">AI 项目经验<span class="x">×</span></span>
          <span class="chip">主数据管理<span class="x">×</span></span>
          <br><button class="btn btn-sm" style="margin-top:4px">+ 添加标签</button>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-hd"><h3>向量索引状态<span class="sub">COL-01~03</span></h3></div>
      <div class="card-bd">
        <table style="font-size:12.5px">
          <tbody>
            <tr><td>resume_vec 简历分块</td><td class="num"><span class="tag tag-ok">已索引 12</span></td></tr>
            <tr><td>skill_vec 技能分块</td><td class="num"><span class="tag tag-ok">已索引 5</span></td></tr>
            <tr><td>project_vec 项目分块</td><td class="num"><span class="tag tag-ok">已索引 3</span></td></tr>
          </tbody>
        </table>
        <div class="alert alert-info" style="margin:12px 0 0"><span class="i">◈</span>
          <div>向量库<b>只存脱敏检索单元</b>（姓名→<span class="ph">{{CANDIDATE}}</span>），
          不含明文身份信息；检索按 <span class="mono">tenant_id + org_id</span> 前置过滤。</div></div>
      </div>
    </div>

    <div class="card">
      <div class="card-hd"><h3>个人信息权利<span class="sub">F-3.11</span></h3></div>
      <div class="card-bd">
        <table style="font-size:12.5px">
          <thead><tr><th>权利</th><th>状态</th></tr></thead>
          <tbody>
            <tr><td>查阅</td><td><span class="tag tag-ok">已响应</span></td></tr>
            <tr><td>更正</td><td><span class="tag tag-info">处理中（72h 时限）</span></td></tr>
            <tr><td>删除</td><td><span class="tag tag-gray">未申请</span></td></tr>
            <tr><td>导出</td><td><span class="tag tag-ok">已生成 2026-09-18</span></td></tr>
          </tbody>
        </table>
        <a class="btn btn-sm" style="margin-top:11px" href="p15-my-profile.html">查看权利台账 →</a>
      </div>
    </div>
  </div>
</div>
"""
    st = states(
        sbox('loading — 详情骨架屏（含履历与时间线占位）', s_loading(6)),
        sbox('masked — 字段级裁剪（MGR / EMP 视角）',
             '<table style="border:1px solid #e3e8ef;border-radius:5px;overflow:hidden">'
             '<thead><tr><th>字段</th><th>HR / ADM 视角</th><th>MGR / EMP 视角</th></tr></thead><tbody>'
             '<tr><td>身份证</td><td class="mono">3301**********1234</td><td class="mono">3301**********1234</td></tr>'
             '<tr><td>手机号</td><td class="mono">13812348888</td><td class="mono">138****8888</td></tr>'
             '<tr><td>邮箱</td><td class="mono">liwei@example.com</td><td class="mono">li***@example.com</td></tr>'
             '</tbody></table>'
             '<div class="form-help">字段级裁剪在<b>序列化层集中实现</b>，不在各接口手写。'
             '响应带 <span class="mono">sensitive_visible:false</span> 供前端渲染 🔒 标识。</div>'),
        sbox('error — 档案不存在 / 已软删（code 40401；越权返回 40301 而非 40401）',
             s_error('档案不存在或已删除', '40401', 'b02c7d1e5a33')),
        sbox('forbidden — MGR 访问非本部门档案（统一 40301，不泄露资源存在性）',
             s_forbidden('业务经理', '仅可访问本部门档案；跨部门访问返回 40301（不返回 404）')),
    )
    leg = legend([
        '<b>对应：</b>F-3.2 / F-3.4 / F-3.10 · API-31 / API-34 / API-55 / API-68 · TBL-11~16 / TBL-19 / TBL-22 / COL-01~03',
        '<b>跳转：</b>编辑 → 字段复核（p10）· 合并留痕 → p11 · 问答 → p14 · 本人权利 → p15',
        '<b>状态：</b>默认 / 加载 / 脱敏 / 错误 / 无权限',
        '<b>注意：</b>所有 id 显示为字符串；敏感字段按角色裁剪；变更留痕只追加不可改',
    ])
    return shell('p08-talent-detail.html', 'P-8', '人才档案详情', '/ 人才档案 / 档案详情',
                 'F-3.2 / F-3.4 / F-3.10', body + st + leg)


# ============================================================ P-9 批量导入与任务进度
def p09():
    body = """
<div class="steps">
  <div class="step done"><span class="n">✓</span>上传文件</div><div class="step-line"></div>
  <div class="step done"><span class="n">✓</span>创建任务</div><div class="step-line"></div>
  <div class="step cur"><span class="n">3</span>解析与入库</div><div class="step-line"></div>
  <div class="step"><span class="n">4</span>生成质量报告</div>
</div>

<div class="grid" style="grid-template-columns:minmax(0,1fr) 348px;align-items:start">
  <div>
    <div class="card">
      <div class="card-hd"><h3>上传简历<span class="sub">API-36 · 支持 multipart + 幂等键</span></h3>
        <span class="muted" style="font-size:12px">单文件 ≤50MB · 单批 ≤100 份</span></div>
      <div class="card-bd">
        <div class="drop">
          <div class="ic">⇪</div>
          <h4>拖拽简历文件到此处，或点击选择</h4>
          <p>支持 PDF / Word / 图片 / 扫描件 / 压缩包（zip 由服务端解包）</p>
          <button class="btn btn-primary">选择文件<span class="mark">①</span></button>
        </div>
        <div class="grid g3" style="margin-top:14px;gap:12px">
          <label class="check"><input type="checkbox" checked> 自动查重（F-3.5）</label>
          <label class="check"><input type="checkbox" checked> 自动打标签（受控词表）</label>
          <div style="display:flex;gap:8px;align-items:center">
            <span style="font-size:13px;color:#5b6673">解析策略</span>
            <select><option selected>自动</option><option>强制 OCR</option><option>仅文本</option></select>
          </div>
        </div>
        <div class="alert alert-info" style="margin:14px 0 0"><span class="i">◈</span>
          <div>单批 100 份已到上限。若需更多请分批提交 —— 超限会返回
          <code>40002</code> 并给出拆分建议，<b>不是静默截断</b>。</div></div>
      </div>
    </div>

    <div class="card">
      <div class="card-hd"><h3>本次文件<span class="sub">100 份 · 合计 268.4 MB</span></h3>
        <button class="btn btn-sm">清空</button></div>
      <div class="filelist">
        <div class="fi"><span>📄</span><span class="nm">简历_李伟.pdf</span>
          <span class="muted" style="font-size:12px">1.2 MB</span><span class="tag tag-ok">解析成功</span></div>
        <div class="fi"><span>📄</span><span class="nm">简历_王强.docx</span>
          <span class="muted" style="font-size:12px">0.8 MB</span><span class="tag tag-ok">解析成功</span></div>
        <div class="fi"><span>🖼</span><span class="nm">简历_赵敏_扫描件.jpg</span>
          <span class="muted" style="font-size:12px">3.4 MB</span><span class="tag tag-ok">OCR 成功</span></div>
        <div class="fi"><span>📄</span><span class="nm">简历_陈立.pdf</span>
          <span class="muted" style="font-size:12px">2.1 MB</span><span class="tag tag-ok">解析成功</span></div>
        <div class="fi"><span>🗜</span><span class="nm">候选人批量_20260921.zip</span>
          <span class="muted" style="font-size:12px">86.7 MB</span><span class="tag tag-info">已解包 62 份</span></div>
        <div class="fi"><span>📄</span><span class="nm">简历_周航_加密.pdf</span>
          <span class="muted" style="font-size:12px">1.5 MB</span><span class="tag tag-danger">文件加密无法读取</span></div>
        <div class="fi"><span>🖼</span><span class="nm">简历_欧阳若曦_拍照.jpg</span>
          <span class="muted" style="font-size:12px">4.2 MB</span><span class="tag tag-danger">OCR 识别失败</span></div>
      </div>
      <div class="pager"><span>展示 7 / 100　失败 4 份</span>
        <button class="btn btn-sm">↻ 重试失败文件<span class="mark">④</span></button></div>
    </div>

    <div class="card">
      <div class="card-hd"><h3>数据质量报告<span class="sub">F-3.8 · API-41</span></h3>
        <div><button class="btn btn-sm">导出 Excel</button>
        <button class="btn btn-sm">导出 PDF</button></div></div>
      <div class="card-bd">
        <div class="grid g4" style="gap:12px;margin-bottom:14px">
          <div class="stat" style="box-shadow:none"><div class="lbl">总数 / 成功</div>
            <div class="num" style="font-size:21px">96<small>/100</small></div>
            <div class="sub">成功率 96%</div></div>
          <div class="stat" style="box-shadow:none"><div class="lbl">字段完整率</div>
            <div class="num" style="font-size:21px">91.3<small>%</small></div>
            <div class="sub">低于 95% 的字段见右表</div></div>
          <div class="stat" style="box-shadow:none"><div class="lbl">待复核</div>
            <div class="num" style="font-size:21px">183<small>条</small></div>
            <div class="sub">高影响 61 · 抽样 12</div></div>
          <div class="stat" style="box-shadow:none"><div class="lbl">重复候选</div>
            <div class="num" style="font-size:21px">7<small>对</small></div>
            <div class="sub">最高相似度 0.94</div></div>
        </div>

        <div class="grid g2" style="gap:14px">
          <div>
            <div style="font-size:13px;font-weight:600;margin-bottom:8px">字段完整率</div>
            <table style="border:1px solid #e3e8ef;border-radius:5px;overflow:hidden">
              <thead><tr><th>字段</th><th class="num">已填/总数</th><th style="width:120px">完整率</th></tr></thead>
              <tbody>
                <tr><td>姓名</td><td class="num">96/96</td>
                  <td><div class="progress ok"><i style="width:100%"></i></div></td></tr>
                <tr><td>学历</td><td class="num">91/96</td>
                  <td><div class="progress ok"><i style="width:94.8%"></i></div></td></tr>
                <tr><td>院校</td><td class="num">88/96</td>
                  <td><div class="progress"><i style="width:91.7%"></i></div></td></tr>
                <tr><td>工作年限</td><td class="num">84/96</td>
                  <td><div class="progress warn"><i style="width:87.5%"></i></div></td></tr>
                <tr><td>项目经历</td><td class="num">76/96</td>
                  <td><div class="progress warn"><i style="width:79.2%"></i></div></td></tr>
              </tbody>
            </table>
          </div>
          <div>
            <div style="font-size:13px;font-weight:600;margin-bottom:8px">失败原因分布</div>
            <table style="border:1px solid #e3e8ef;border-radius:5px;overflow:hidden">
              <thead><tr><th>原因</th><th class="num">数量</th></tr></thead>
              <tbody>
                <tr><td>扫描件 OCR 识别失败</td><td class="num">2</td></tr>
                <tr><td>文件加密无法读取</td><td class="num">1</td></tr>
                <tr><td>格式不支持</td><td class="num">1</td></tr>
              </tbody>
            </table>
            <div class="alert alert-warn" style="margin:12px 0 0"><span class="i">◈</span>
              <div><b>准确率需离线评估，不在本报告内。</b><br>
              字段级准确率 ≥95%（IDX-02）由金标准集（≥200 份人工标注）离线评估得出 ——
              接口内联准确率会让人误以为可实时计算。</div></div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div>
    <div class="card">
      <div class="card-hd"><h3>任务进度<span class="sub">IMP20260921001</span></h3>
        <span class="tag tag-info">解析中</span></div>
      <div class="card-bd">
        <div style="display:flex;justify-content:space-between;font-size:12.5px;color:#5b6673;margin-bottom:6px">
          <span>SSE 实时推送（API-39）</span><span><b>62%</b></span></div>
        <div class="progress"><i style="width:62%"></i></div>
        <table style="font-size:12.5px;margin-top:14px">
          <tbody>
            <tr><td>已处理</td><td class="num">62 / 100</td></tr>
            <tr><td>成功</td><td class="num">59</td></tr>
            <tr><td>失败</td><td class="num">3</td></tr>
            <tr><td>预计剩余</td><td class="num">约 6 分钟</td></tr>
            <tr><td>Agent 任务</td><td class="num mono">1839204770000008001</td></tr>
          </tbody>
        </table>
        <div class="form-help" style="margin-top:10px">
          断线后可用同一 task_id 重新订阅；心跳 15s（<span class="mono">event: ping</span>）。
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-hd"><h3>解析流水线</h3></div>
      <div class="card-bd">
        <div class="timeline" style="padding-left:20px">
          <div class="tl-item"><div class="tl-head"><b>格式识别</b><span class="muted">0.2s</span></div></div>
          <div class="tl-item"><div class="tl-head"><b>OCR / 文本提取</b><span class="muted">1.4s</span></div></div>
          <div class="tl-item ai"><div class="tl-head"><b>字段抽取（本地 qwen2.5:3b）</b><span class="muted">3.1s</span></div>
            <div class="tl-body">数据不出网：含个人隐私，走本地模型</div></div>
          <div class="tl-item"><div class="tl-head"><b>字典归一化</b><span class="muted">0.1s</span></div></div>
          <div class="tl-item"><div class="tl-head"><b>置信度判定</b><span class="muted">0.1s</span></div>
            <div class="tl-body">低于阈值进入复核队列（F-3.3 漏斗）</div></div>
          <div class="tl-item cur"><div class="tl-head"><b>入库 + 向量索引</b><span class="tag tag-info">进行中</span></div>
            <div class="tl-body">脱敏后写入 Milvus（COL-01~03）</div></div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-hd"><h3>导入任务历史</h3>
        <a class="btn btn-sm" href="#">全部</a></div>
      <table style="font-size:12.5px">
        <tbody>
          <tr><td class="mono">IMP20260918003</td><td><span class="tag tag-warn">部分失败</span></td>
            <td class="num muted">58/60</td></tr>
          <tr><td class="mono">IMP20260912001</td><td><span class="tag tag-ok">已完成</span></td>
            <td class="num muted">200/200</td></tr>
          <tr><td class="mono">IMP20260905002</td><td><span class="tag tag-danger">失败</span></td>
            <td class="num muted">0/12</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>
"""
    st = states(
        sbox('loading — 上传区与任务进度骨架屏', s_loading(5)),
        sbox('error — 单文件超限（code 41301）',
             '<div class="alert alert-danger" style="margin:0"><span class="i">!</span>'
             '<div><b>存在超过 50MB 的文件，请压缩后重试</b><br>'
             '简历_陈某某.pdf（62.4 MB）<code>（code 41301 / data.oversize_files[]）</code></div></div>'),
        sbox('error — 格式不支持（code 41501）',
             '<div class="alert alert-danger" style="margin:0"><span class="i">!</span>'
             '<div><b>格式不支持：简历_王五.wps</b><br>支持：pdf / doc / docx / jpg / png / zip</div></div>'),
        sbox('error — 任务未完成时取质量报告（code 42201）',
             '<div class="alert alert-warn" style="margin:0"><span class="i">⏳</span>'
             '<div>任务尚未完成，暂无法生成质量报告（当前状态：解析中 62%）</div></div>'),
        sbox('forbidden — MGR / EMP / AIOPS 无导入权限',
             s_forbidden('业务经理', '仅可查看本部门档案，导入需 ADM / HR')),
    )
    leg = legend([
        '<b>对应：</b>F-3.1 / F-3.8 · API-36~43 · TBL-20 / TBL-21 / TBL-25',
        '<b>状态：</b>默认 / 上传中 / 解析中 / 超限 / 格式错 / 未完成 / 无权限',
        '<b>红线：</b>单批 ≤100 份（=100 时给 warn，>100 直接拒绝）；失败文件不阻塞整批；原始文件留存供复核对照',
        '<b>注意：</b>失败重试是单文件级（API-43），不是整任务重跑',
    ])
    return shell('p09-talent-import.html', 'P-9', '批量导入与任务进度', '/ 人才档案 / 批量导入',
                 'F-3.1 / F-3.8', body + st + leg)


# ============================================================ P-10 字段复核工作台
def p10():
    body = """
<div class="grid g4" style="margin-bottom:14px">
  <div class="stat"><div class="lbl">本轮待复核<span class="mark">①</span></div>
    <div class="num">61<small>条</small></div>
    <div class="sub">高影响面（置信度 &lt; 0.85）</div></div>
  <div class="stat"><div class="lbl">低影响面抽样</div><div class="num">12<small>条</small></div>
    <div class="sub">10% 抽样，仅用于监控抽取质量</div></div>
  <div class="stat"><div class="lbl">平均单条耗时</div><div class="num">20<small>s</small></div>
    <div class="sub">复核人力估算 ≈ 20 分钟</div></div>
  <div class="stat"><div class="lbl">复核后准确率提升</div><div class="num up">+3.8<small>pt</small></div>
    <div class="sub">91.3% → 95.1%（口径见 F-3.3.1）</div></div>
</div>

<div class="alert alert-warn"><span class="i">◈</span>
  <div><b>漏斗规则（D-8，必须落地）：</b>高影响字段（姓名 / 学历 / 院校 / 工作年限 / 任职经历 / 技能）
  置信度 &lt; 0.85 全部入队；低影响字段（兴趣 / 自我评价 / 期望薪资）<b>不入默认队列</b>，
  仅按 10% 抽样；单批 &gt;200 条时强制提示拆批。<br>
  背景：300 份 × 20 字段 × 30% ≈ 1800 次确认，人力不可行 —— 全量复核是本项目最典型的伪需求。</div></div>

<div class="toolbar">
  <select><option selected>高影响面（默认）</option><option>低影响面抽样</option><option>全部</option></select>
  <select><option>全部字段</option><option selected>工作年限</option><option>院校</option><option>技能</option></select>
  <input type="text" placeholder="置信度上限" value="0.85" style="min-width:110px">
  <button class="btn">查询</button>
  <span class="grow"></span>
  <span class="muted" style="font-size:12.5px">已选 <b>2</b> 条</span>
  <button class="btn">采纳原文</button>
  <button class="btn btn-primary">批量确认<span class="mark">③</span></button>
</div>

<div class="card">
  <div class="card-hd"><h3>复核队列<span class="sub">API-44 · 默认只返回高影响面低置信条目</span></h3>
    <span class="muted" style="font-size:12px">IMP20260921001 · 共 61 条</span></div>
  <table>
    <thead><tr><th style="width:36px"><input type="checkbox"></th><th>人才</th><th>字段</th>
      <th>原文</th><th>AI 抽取值</th><th>置信度</th><th>影响面</th><th>来源</th><th>操作</th></tr></thead>
    <tbody>
      <tr style="background:#fafcff">
        <td><input type="checkbox" checked></td>
        <td><a href="p08-talent-detail.html">李伟</a></td><td>工作年限</td>
        <td class="muted">6 年（2019-07 至今）</td><td><b>6</b></td>
        <td><span class="tag tag-danger">0.72</span></td>
        <td><span class="tag tag-warn">高影响</span></td>
        <td class="muted">简历_李伟.pdf p1</td>
        <td class="ops"><a href="#diff">对照</a><a href="#">采纳</a></td></tr>
      <tr>
        <td><input type="checkbox" checked></td>
        <td><a href="p08-talent-detail.html">王强</a></td><td>院校</td>
        <td class="muted">某某理工大学（原某某工学院）</td><td><b>某某理工大学</b></td>
        <td><span class="tag tag-danger">0.68</span></td>
        <td><span class="tag tag-warn">高影响</span></td>
        <td class="muted">简历_王强.docx p1</td>
        <td class="ops"><a href="#diff">对照</a><a href="#">采纳</a></td></tr>
      <tr>
        <td><input type="checkbox"></td>
        <td><a href="p08-talent-detail.html">赵敏</a></td><td>技能</td>
        <td class="muted">熟悉数据标准、数据资产目录编制</td><td><b>数据标准</b></td>
        <td><span class="tag tag-warn">0.81</span></td>
        <td><span class="tag tag-warn">高影响</span></td>
        <td class="muted">简历_赵敏_扫描件.jpg</td>
        <td class="ops"><a href="#diff">对照</a><a href="#">采纳</a></td></tr>
      <tr>
        <td><input type="checkbox"></td>
        <td><a href="p08-talent-detail.html">陈立</a></td><td>任职经历</td>
        <td class="muted">2013.04—2016.08 某某研究院 研究员</td><td><b>某某研究院 · 研究员</b></td>
        <td><span class="tag tag-warn">0.79</span></td>
        <td><span class="tag tag-warn">高影响</span></td>
        <td class="muted">简历_陈立.pdf p2</td>
        <td class="ops"><a href="#diff">对照</a><a href="#">采纳</a></td></tr>
      <tr>
        <td><input type="checkbox"></td>
        <td><a href="p08-talent-detail.html">欧阳若曦</a></td><td>自我评价 <span class="tag tag-gray">低影响</span></td>
        <td class="muted">热爱组织发展工作，善于沟通协调…</td><td><b>组织发展 / 沟通协调</b></td>
        <td><span class="tag tag-danger">0.55</span></td>
        <td><span class="tag tag-gray">低影响</span></td>
        <td class="muted">抽样 10%</td>
        <td class="ops"><a href="#diff">对照</a><a href="#">采纳</a></td></tr>
    </tbody>
  </table>
  <div class="pager"><span>共 61 条　每页 20 条　平均单条 20s</span>
    <div class="pages"><span>‹</span><span class="cur">1</span><span>2</span><span>3</span><span>4</span><span>›</span></div></div>
</div>

<div class="card" id="diff">
  <div class="card-hd"><h3>并排对照<span class="sub">API-45 · 原文 ↔ 抽取结果</span></h3>
    <span class="tag tag-warn">字段：工作年限 · 置信度 0.72</span></div>
  <div class="card-bd">
    <div class="diff">
      <div>
        <h5><span>简历原文（来源：简历_李伟.pdf · page=1, block=3）</span><span class="muted">只读</span></h5>
        <p>工作经历：<br>
          2019 年 7 月至今，先后在某某科技有限公司、某某集团有限公司从事数据相关工作，累计
          <span class="hl">6 年</span>，其中数据治理方向 3 年。</p>
        <p class="muted" style="font-size:12px">原文中「6 年」为累计年限表述，未直接给出单一雇主年限。</p>
      </div>
      <div>
        <h5><span>AI 抽取结果</span><span class="muted">可编辑</span></h5>
        <p>字段：<b>work_years（工作年限）</b></p>
        <p><input type="text" value="6" style="width:110px"> <span class="muted">年</span></p>
        <p class="muted" style="font-size:12px">置信度 0.72 &lt; 阈值 0.85 → 进入复核队列。<br>
        归一化规则：education_level 取字典码（见 TBL-10）。</p>
        <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn btn-sm btn-primary">采纳 AI 值</button>
          <button class="btn btn-sm">采纳原文值</button>
          <button class="btn btn-sm">标记免复核</button>
        </div>
      </div>
    </div>
    <div class="alert alert-info" style="margin:14px 0 0"><span class="i">◈</span>
      <div>每次确认 / 修正<b>都会写变更留痕</b>（TBL-22，change_source=1 人工复核），
      响应回传 <span class="mono">change_log_ids[]</span> 供前端提供「撤销本次确认」。<br>
      并发冲突采用<b>部分成功</b>（code=0 + <span class="mono">conflicted[]</span>），
      不整批回滚 —— 否则一个并发冲突会让 HR 丢掉整批复核结果。</div></div>
  </div>
</div>
"""
    st = states(
        sbox('loading — 队列与对照区骨架屏', s_loading(5)),
        sbox('empty — 本轮无需复核（全部字段置信度达标）',
             s_empty('太棒了，本轮没有待复核字段', '所有高影响字段置信度均 ≥ 0.85，无需人工介入',
                     '<a class="btn" href="p09-talent-import.html">返回导入任务</a>')),
        sbox('warning — 待复核超 200 条，强制提示拆批（API-44 meta.warning）',
             '<div class="alert alert-warn" style="margin:0"><span class="i">◈</span>'
             '<div><b>本轮待复核 386 条，超过 200 条建议拆批处理</b><br>'
             '提示是非阻断的（code 仍为 0）；真正阻断在「批量确认」单次 &gt;200 且未加 force 时返回 40002。</div></div>'),
        sbox('conflict — 批量确认中部分条目被他人处理（code=0 + conflicted[]）',
             '<table style="border:1px solid #e3e8ef;border-radius:5px;overflow:hidden">'
             '<thead><tr><th>field_id</th><th>结果</th><th>处理人</th></tr></thead><tbody>'
             '<tr><td class="mono">…009001</td><td><span class="tag tag-ok">已确认</span></td><td>—</td></tr>'
             '<tr><td class="mono">…009002</td><td><span class="tag tag-danger">冲突跳过</span></td>'
             '<td>王强 15:58</td></tr>'
             '</tbody></table>'
             '<div class="form-help">前端高亮 <span class="mono">conflicted[]</span> 并提示刷新；已成功的条目<b>不回滚</b>。</div>'),
        sbox('forbidden — MGR / AIOPS 无复核权限',
             s_forbidden('AI 运维', '仅可管理模型与日志，不可复核业务字段')),
    )
    leg = legend([
        '<b>对应：</b>F-3.2 / F-3.3 / F-3.3.1 · API-44~49 · TBL-16 / TBL-22 / TBL-11',
        '<b>状态：</b>默认 / 加载 / 空 / 超量提示 / 并发冲突 / 无权限',
        '<b>红线：</b>不得全量复核；低影响字段只 10% 抽样；每次变更写留痕；并发冲突部分成功',
        '<b>对应看板：</b>F-3.3.1 复核工作量看板已并入本页 KPI 区（平均耗时 / 人力估算 / 准确率提升）',
    ])
    return shell('p10-talent-review.html', 'P-10', '字段复核工作台', '/ 人才档案 / 字段复核',
                 'F-3.3 / F-3.3.1', body + st + leg)


# ============================================================ P-11 重复档案合并
def p11():
    body = """
<div class="alert alert-warn"><span class="i">◈</span>
  <div><b>合并是不可逆的高风险操作</b>，但可通过「快照回滚」还原（F-3.5 + F-3.10）。
  合并前必须生成 <span class="mono">snapshot_id</span>，否则回滚无法执行 —— 这是 F-3.10 明确依赖的机制。</div></div>

<div class="toolbar">
  <select><option selected>待处理</option><option>已合并</option><option>已忽略</option></select>
  <select><option>相似度不限</option><option selected>≥ 0.90</option><option>≥ 0.95</option></select>
  <button class="btn">查询</button>
  <span class="grow"></span>
  <button class="btn">重新扫描查重</button>
</div>

<div class="grid" style="grid-template-columns:400px minmax(0,1fr);align-items:start">
  <div class="card" style="margin-bottom:0">
    <div class="card-hd"><h3>重复候选对<span class="sub">TBL-17 · 7 对</span></h3></div>
    <table style="font-size:13px">
      <thead><tr><th>候选对</th><th>相似度</th><th>状态</th></tr></thead>
      <tbody>
        <tr style="background:#eaf1fd"><td><b>李伟</b> ↔ 李伟（重复）<br>
          <span class="muted" style="font-size:11.5px">数据治理部 / 技术研发中心</span></td>
          <td><span class="tag tag-danger">0.94</span></td><td><span class="tag tag-warn">待处理</span></td></tr>
        <tr><td>王强 ↔ 王强<br><span class="muted" style="font-size:11.5px">技术研发中心 / 技术研发中心</span></td>
          <td><span class="tag tag-danger">0.93</span></td><td><span class="tag tag-warn">待处理</span></td></tr>
        <tr><td>赵敏 ↔ 赵敏（曾用名）<br><span class="muted" style="font-size:11.5px">数据治理部 / 数据治理部</span></td>
          <td><span class="tag tag-warn">0.91</span></td><td><span class="tag tag-warn">待处理</span></td></tr>
        <tr><td>欧阳若曦 ↔ 欧阳若熙<br><span class="muted" style="font-size:11.5px">人力资源部 / 人力资源部</span></td>
          <td><span class="tag tag-warn">0.90</span></td><td><span class="tag tag-warn">待处理</span></td></tr>
        <tr><td>陈立 ↔ 陈力<br><span class="muted" style="font-size:11.5px">集团总部 / 集团总部</span></td>
          <td><span class="tag tag-gray">0.86</span></td><td><span class="tag tag-gray">已忽略</span></td></tr>
        <tr><td>周航 ↔ 周航（旧）<br><span class="muted" style="font-size:11.5px">审计部 / 审计部</span></td>
          <td><span class="tag tag-gray">0.88</span></td><td><span class="tag tag-ok">已合并</span></td></tr>
      </tbody>
    </table>
    <div class="legend" style="padding:10px 13px;margin:0;border-top:1px solid #e3e8ef">
      <b>判定：</b>向量相似度 + 关键字段比对，阈值可配（默认 0.90）<br>
      <b>阈值判定是确定性的</b>，不交给大模型
    </div>
  </div>

  <div>
    <div class="card">
      <div class="card-hd"><h3>并排对比<span class="sub">候选对 #1839204770000012001</span></h3>
        <span class="tag tag-danger">相似度 0.94</span></div>
      <div class="card-bd">
        <div class="diff">
          <div>
            <h5><span>主档 A · 李伟</span><span><input type="radio" name="master" checked> 设为主档<span class="mark">①</span></span></h5>
            <p>部门：数据治理部<br>学历：硕士 · 某某大学<br>工作年限：<span class="hl-ok">6 年</span><br>
              电话：<span class="mono">13812348888</span><br>技能：数据治理、主数据管理、SQL<br>
              履历：工作 2 / 项目 3 / 技能 6 / 证书 2</p>
            <div class="muted" style="font-size:12px">创建 2026-03-02 · 更新 2026-09-21 · version_no 3</div>
          </div>
          <div>
            <h5><span>从档 B · 李伟（重复）</span><span><input type="radio" name="master"> 设为主档</span></h5>
            <p>部门：技术研发中心<br>学历：硕士 · 某某大学<br>工作年限：<span class="hl">5 年</span><br>
              电话：<span class="mono">13812348888</span><br>技能：数据治理、Python<br>
              履历：工作 1 / 项目 2 / 技能 3 / 证书 1</p>
            <div class="muted" style="font-size:12px">创建 2026-08-18 · 更新 2026-08-18 · version_no 1</div>
          </div>
        </div>

        <div class="grid g3" style="margin-top:14px;gap:12px;align-items:end">
          <div><div class="muted" style="font-size:12px;margin-bottom:4px">冲突字段取值</div>
            <label class="radio"><input type="radio" name="wy" checked> 取 A（6 年）</label>
            <label class="radio"><input type="radio" name="wy"> 取 B（5 年）</label></div>
          <div><div class="muted" style="font-size:12px;margin-bottom:4px">合并选项</div>
            <label class="check"><input type="checkbox" checked> 合并标签</label>
            <label class="check"><input type="checkbox" checked> 合并履历明细</label></div>
          <div><div class="muted" style="font-size:12px;margin-bottom:4px">合并后</div>
            <div class="muted" style="font-size:12.5px">履历：工作 3 / 项目 5 / 技能 7 / 证书 3</div></div>
        </div>

        <div class="alert alert-info" style="margin:14px 0 12px"><span class="i">◈</span>
          <div>合并动作：① 生成快照 → ② 写主档 → ③ 迁明细 → ④ 软删从档（data_status=2 已合并）
          → ⑤ <b>同步删除从档 Milvus 向量</b> → ⑥ 重建主档向量。<br>
          从档软删而非物理删；合并写 <span class="mono">talent_change_log</span>（change_source=2）。</div></div>

        <div style="display:flex;gap:9px">
          <button class="btn btn-primary">确认合并<span class="mark">②</span></button>
          <button class="btn">忽略该候选对</button>
          <button class="btn btn-danger">稍后处理</button>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-hd"><h3>最近合并记录<span class="sub">可回滚</span></h3></div>
      <table>
        <thead><tr><th>合并时间</th><th>主档</th><th>从档</th><th>合并字段</th><th>迁移明细</th><th>快照</th><th>操作</th></tr></thead>
        <tbody>
          <tr><td class="muted nowrap">09-21 16:20</td><td>周航</td><td>周航（旧）</td><td>2 项</td><td>11 条</td>
            <td class="mono muted">…13002</td><td class="ops"><a href="#">回滚<span class="mark">③</span></a></td></tr>
          <tr><td class="muted nowrap">08-20 16:02</td><td>李伟</td><td>李伟（重复）</td><td>2 项</td><td>11 条</td>
            <td class="mono muted">…10302</td><td class="ops"><a href="#">回滚</a></td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>
"""
    st = states(
        sbox('loading — 候选对与对比区骨架屏', s_loading(5)),
        sbox('empty — 无重复候选', s_empty('没有发现重复档案', '本次导入的简历未检出高相似度重复对（阈值 0.90）',
                                            '<button class="btn">重新扫描查重</button>')),
        sbox('conflict — 该候选对已被处理（code 40901）',
             '<div class="alert alert-warn" style="margin:0"><span class="i">!</span>'
             '<div><b>该重复候选对已合并，不能重复操作</b><br>'
             'merge_id: …12000　合并时间：2026-09-21 13:40<code>（code 40901）</code></div></div>'),
        sbox('error — 合并过程中向量删除失败（由补偿脚本收敛）',
             '<div class="alert alert-danger" style="margin:0"><span class="i">!</span>'
             '<div><b>从档向量清理未完成</b><br>档案已标记「检索不可用」，由 rebuild_index.py 补偿重建；'
             '不影响档案本身可用性。<br>跨库操作无分布式事务 → 统一「先动可恢复的，后动不可恢复的」。</div></div>'),
        sbox('forbidden — MGR / EMP 无合并权限',
             s_forbidden('业务经理', '仅可查看本部门档案，不可执行合并与回滚')),
    )
    leg = legend([
        '<b>对应：</b>F-3.5 / F-3.10 · API-57~61 · TBL-17 / TBL-11 / TBL-22 / COL-01~03',
        '<b>状态：</b>默认 / 加载 / 空 / 冲突 / 向量失败 / 无权限',
        '<b>红线：</b>必须生成 snapshot_id 才能回滚；从档软删 + 向量同步删除；主档画像必须重建',
        '<b>注意：</b>阈值判定确定性（不交大模型）；由 Agent 自动触发的合并建议必须走人工待办（F-A0.3）',
    ])
    return shell('p11-talent-dedup.html', 'P-11', '重复档案合并', '/ 人才档案 / 重复合并',
                 'F-3.5 / F-3.10', body + st + leg)


# ============================================================ P-12 语义人才检索
def p12():
    body = """
<div class="alert alert-warn"><span class="i">◈</span>
  <div><b>权限前置（B-8 整改的核心落点）：</b>服务端从 JWT 生成
  <span class="mono">expr = tenant_id == T and org_id in [...]</span>，<b>检索前</b>作为过滤条件下发。
  严禁「先召回全量再应用层过滤」—— 那会把无权数据放进大模型上下文，构成数据越权。<br>
  降级为关键词检索时<b>仍保留权限表达式</b>，不得以「检索能力受限」为由放宽权限。</div></div>

<div class="card">
  <div class="card-bd">
    <div style="display:flex;gap:9px;align-items:flex-start">
      <textarea rows="2" style="flex:1" placeholder="用自然语言描述你要找的人，例如：找擅长数字化转型、3 年以上 AI 项目经验的骨干">找擅长数字化转型、3 年以上 AI 项目经验的骨干</textarea>
      <button class="btn btn-primary" style="height:auto;padding:9px 18px">检索<span class="mark">①</span></button>
    </div>
    <div style="display:flex;gap:9px;align-items:center;margin-top:12px;flex-wrap:wrap">
      <select><option>混合检索（推荐）</option><option>纯向量</option><option>纯关键词</option></select>
      <select><option>返回 10 条</option><option>返回 20 条</option><option>返回 50 条</option></select>
      <select><option>数据治理部</option><option>全部（本部门范围内）</option></select>
      <select><option>学历不限</option><option selected>本科及以上</option></select>
      <label class="check"><input type="checkbox" checked> 启用 Rerank 精排</label>
      <label class="check"><input type="checkbox" checked> 显示命中理由</label>
    </div>
    <div class="alert alert-info" style="margin:14px 0 0"><span class="i">◈</span>
      <div>本次检索：<b>mode_used = hybrid</b> · <b>degraded = false</b> ·
      <b>permission_filter_applied = true</b> ·
      权限范围 <span class="mono">tenant_id=183…001, org_ids=[183…100]</span> ·
      候选池 382 · 耗时 412ms</div></div>
  </div>
</div>

<div class="card">
  <div class="card-hd"><h3>检索结果<span class="sub">命中 10 条 · 按相关度排序</span></h3>
    <span class="muted" style="font-size:12px">命中片段为脱敏文本，保持占位符形态展示</span></div>
  <div class="card-bd">
    <div class="card" style="margin-bottom:12px;box-shadow:none">
      <div class="card-bd">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
          <div>
            <div style="font-size:15px;font-weight:700">李伟
              <span class="muted" style="font-weight:400;font-size:12.5px">· 数据治理部 · 硕士 · 6 年</span>
              <span class="tag tag-ok" style="margin-left:6px">匹配 0.8734</span></div>
            <div style="margin-top:9px;font-size:12.5px;color:#5b6673">
              <div style="font-weight:600;color:#1c2430;margin-bottom:4px">命中理由</div>
              <div>① 项目经历「集团主数据平台」与「数字化转型」高度相关</div>
              <div>② 技能命中「AI 项目经验」「数据治理」</div>
              <div>③ 工作年限 6 年 ≥ 要求 3 年</div>
            </div>
          </div>
          <div style="text-align:right;flex:0 0 auto">
            <a class="btn btn-sm" href="p08-talent-detail.html">查看档案</a>
            <a class="btn btn-sm" href="p14-talent-ask.html" style="margin-top:6px">就这个人提问</a>
          </div>
        </div>
        <div class="cite">
          <div class="hd"><span>片段 1 · 项目经历</span><span class="mono">score 0.89</span></div>
          <div class="txt">主导 <span class="ph">{{CANDIDATE}}</span> 所在集团主数据平台建设，覆盖 12 个业务域，负责数据标准与主数据管理</div>
        </div>
        <div class="cite">
          <div class="hd"><span>片段 2 · 技能</span><span class="mono">score 0.86</span></div>
          <div class="txt">数据治理（精通）、主数据管理（熟练）、SQL（精通）</div>
        </div>
      </div>
    </div>

    <div class="card" style="margin-bottom:12px;box-shadow:none">
      <div class="card-bd">
        <div style="font-size:15px;font-weight:700">王强
          <span class="muted" style="font-weight:400;font-size:12.5px">· 技术研发中心 · 本科 · 8 年</span>
          <span class="tag tag-ok" style="margin-left:6px">匹配 0.8102</span></div>
        <div style="margin-top:9px;font-size:12.5px;color:#5b6673">
          <div style="font-weight:600;color:#1c2430;margin-bottom:4px">命中理由</div>
          <div>① 项目经历命中「数字化转型」关键词</div>
          <div>② 技能命中「架构设计」</div></div>
        <div class="cite">
          <div class="hd"><span>片段 1 · 项目经历</span><span class="mono">score 0.83</span></div>
          <div class="txt">负责企业数字化转型技术架构设计，牵头中台化改造</div>
        </div>
      </div>
    </div>

    <div class="card" style="box-shadow:none">
      <div class="card-bd">
        <div style="font-size:15px;font-weight:700">赵敏
          <span class="muted" style="font-weight:400;font-size:12.5px">· 数据治理部 · 硕士 · 4 年</span>
          <span class="tag tag-warn" style="margin-left:6px">匹配 0.6621</span></div>
        <div style="margin-top:9px;font-size:12.5px;color:#5b6673">
          <div style="font-weight:600;color:#1c2430;margin-bottom:4px">命中理由</div>
          <div>① 技能命中「数据标准」，与数据治理方向部分相关</div>
          <div>② 工作年限 4 年 ≥ 要求 3 年（<span class="muted">但 AI 项目经验不足</span>）</div></div>
      </div>
    </div>
  </div>
</div>
"""
    st = states(
        sbox('loading — 结果卡片骨架屏', s_loading(5)),
        sbox('empty — 无相关结果',
             s_empty('没有找到匹配的人才', '试试换一种说法，或放宽年限 / 学历条件',
                     '<button class="btn">清空条件重试</button>')),
        sbox('degraded — Milvus 异常，自动降级为关键词检索（HTTP 200 + code=0）',
             '<div class="alert alert-warn" style="margin:0"><span class="i">◈</span>'
             '<div><b>已降级为关键词检索</b>（mode_used = keyword，degraded = true）<br>'
             'Milvus 不可用，语义相关度可能下降；<b>权限过滤仍然生效</b>（permission_filter_applied = true）。<br>'
             '注意：<b>降级不返回 500</b> —— HTTP 200 + code=0 + degraded=true，前端渲染降级徽标，不按错误处理。</div></div>'),
        sbox('forbidden — filters.org_ids 超出数据域（收窄合法，放宽 40301）',
             '<div class="alert alert-danger" style="margin:0"><span class="i">🔒</span>'
             '<div><b>筛选的组织范围超出您的数据权限</b><br>'
             'requested: [183…200]　allowed: [183…100]<br>'
             '越权尝试<b>不静默忽略</b>而是显式 40301 —— 让越权可见、可审计。</div></div>'),
        sbox('forbidden — EMP 无检索权限', s_forbidden('普通员工', '仅可查看本人画像，语义检索需 ADM / HR / MGR')),
    )
    leg = legend([
        '<b>对应：</b>F-3.6 · API-62 · COL-01~03 / TBL-11　<b>指标：</b>Recall@10 ≥ 85%（金标准查询集 ≥50 条，IDX-01）',
        '<b>状态：</b>默认 / 加载 / 无结果 / 降级 / 越权 / 无权限',
        '<b>红线：</b>检索前置权限过滤 + 降级保留权限表达式；越权传参 40301；检索行为写审计日志',
        '<b>注意：</b>返回附命中理由（可解释）；片段为脱敏文本，前端<b>不做本地回填</b>（真名由 name 字段单独给出）',
    ])
    return shell('p12-talent-search.html', 'P-12', '语义人才检索', '/ 人才档案 / 语义检索',
                 'F-3.6', body + st + leg)


# ============================================================ P-13 标签字典管理
def p13():
    tree_css = """
.tree{font-size:13.5px;padding:4px 2px}
.nd{display:flex;align-items:center;gap:6px;padding:6px 8px;border-radius:5px;color:#5b6673}
.nd:hover{background:#eef2f7}
.nd.on{background:#eaf1fd;color:#1b5fd9;font-weight:600}
.nd .cw{width:12px;color:#98a2ae;font-size:11px}
.nd .cnt{margin-left:auto;font-size:11.5px;color:#98a2ae}
.lv2{padding-left:22px} .lv3{padding-left:44px}
"""
    body = """
<div class="alert alert-warn"><span class="i">◈</span>
  <div><b>标签必须来自受控词表，AI 不得自由造词。</b>
  AI 抽取时只能「从字典中选取」；需新增标签时走「<b>AI 建议 → 管理员审核 → 入库</b>」流程。<br>
  理由：标签漂移会导致按标签做的所有统计与筛选结果<b>不可复现</b>。目标 ≥100 个可用标签。</div></div>

<div class="grid" style="grid-template-columns:272px minmax(0,1fr);align-items:start">
  <div class="card" style="margin-bottom:0">
    <div class="card-hd"><h3>三级分类<span class="sub">TBL-18</span></h3></div>
    <div class="tree">
      <div class="nd"><span class="cw">▾</span>◈ 全部标签<span class="cnt">118</span></div>
      <div class="nd lv2 on"><span class="cw">▾</span>◈ 技能能力<span class="cnt">64</span></div>
      <div class="nd lv3"><span class="cw">　</span>◌ 数据类<span class="cnt">21</span></div>
      <div class="nd lv3"><span class="cw">　</span>◌ AI 类<span class="cnt">18</span></div>
      <div class="nd lv3"><span class="cw">　</span>◌ 研发类<span class="cnt">25</span></div>
      <div class="nd lv2"><span class="cw">▸</span>◈ 经验履历<span class="cnt">26</span></div>
      <div class="nd lv2"><span class="cw">▸</span>◈ 素质特质<span class="cnt">18</span></div>
      <div class="nd lv2"><span class="cw">▸</span>◈ 管理能力<span class="cnt">10</span></div>
    </div>
    <div class="legend" style="padding:10px 13px;margin:0;border-top:1px solid #e3e8ef">
      <b>要求：</b>支持三级分类 + 同义词映射<br>
      <b>硬约束：</b><span class="mono">tag_rel.tag_code</span> 必须取自<b>启用</b>项
    </div>
  </div>

  <div>
    <div class="toolbar" style="margin-top:0">
      <input type="text" placeholder="搜索标签名 / 标签码">
      <select><option selected>全部状态</option><option>启用</option><option>待审核</option><option>停用</option></select>
      <button class="btn">查询</button>
      <span class="grow"></span>
      <button class="btn">导出词表</button>
      <button class="btn btn-primary">+ 新增标签</button>
    </div>

    <div class="card">
      <div class="card-hd"><h3>技能能力 · 数据类<span class="sub">21 个标签</span></h3>
        <span class="muted" style="font-size:12px">字典变更留痕（TBL-22）</span></div>
      <table>
        <thead><tr><th>标签码</th><th>标签名</th><th>同义词</th><th>来源</th><th>使用数</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
          <tr><td class="mono">SKILL_DATA_GOV</td><td><b>数据治理</b></td>
            <td class="muted">数据管控 / Data Governance</td>
            <td><span class="tag tag-info">AI 建议</span></td><td class="num">42</td>
            <td><span class="tag tag-ok">启用</span></td>
            <td class="ops"><a href="#">编辑</a><a href="#">停用</a></td></tr>
          <tr><td class="mono">SKILL_MDM</td><td>主数据管理</td>
            <td class="muted">MDM</td><td><span class="tag tag-gray">人工</span></td>
            <td class="num">28</td><td><span class="tag tag-ok">启用</span></td>
            <td class="ops"><a href="#">编辑</a><a href="#">停用</a></td></tr>
          <tr><td class="mono">SKILL_DATA_STD</td><td>数据标准</td>
            <td class="muted">数据规范</td><td><span class="tag tag-info">AI 建议</span></td>
            <td class="num">19</td><td><span class="tag tag-ok">启用</span></td>
            <td class="ops"><a href="#">编辑</a><a href="#">停用</a></td></tr>
          <tr><td class="mono">SKILL_DATA_QUAL</td><td>数据质量</td>
            <td class="muted">数据校验</td><td><span class="tag tag-gray">人工</span></td>
            <td class="num">16</td><td><span class="tag tag-ok">启用</span></td>
            <td class="ops"><a href="#">编辑</a><a href="#">停用</a></td></tr>
          <tr style="background:#fffdf6"><td class="mono">SKILL_LLM_OPS</td><td><b>大模型运维</b> <span class="tag tag-warn">AI 建议</span></td>
            <td class="muted">LLM Ops</td><td><span class="tag tag-info">AI 建议</span></td>
            <td class="num muted">—</td><td><span class="tag tag-warn">待审核</span></td>
            <td class="ops"><a href="#sug">审核<span class="mark">①</span></a></td></tr>
          <tr><td class="mono">SKILL_KG</td><td>知识图谱</td>
            <td class="muted">KG</td><td><span class="tag tag-gray">人工</span></td>
            <td class="num">7</td><td><span class="tag tag-gray">停用</span></td>
            <td class="ops"><a href="#">编辑</a><a href="#">启用</a></td></tr>
        </tbody>
      </table>
      <div class="pager"><span>共 21 条　每页 20 条</span>
        <div class="pages"><span>‹</span><span class="cur">1</span><span>2</span><span>›</span></div></div>
    </div>

    <div class="card" id="sug">
      <div class="card-hd"><h3>AI 建议标签审核<span class="sub">API-53 / API-54</span></h3>
        <span class="tag tag-warn">待审核 3 条</span></div>
      <div class="card-bd">
        <div class="diff" style="grid-template-columns:1fr 1fr">
          <div>
            <h5><span>AI 建议内容</span><span class="muted">不得直接入库</span></h5>
            <p>建议标签名：<b>大模型运维</b><br>
              建议标签码：<span class="mono">SKILL_LLM_OPS</span><br>
              归属分类：技能能力 / AI 类<br>
              同义词：LLM Ops、模型运维<br>
              建议理由：近 30 天内 14 份简历出现「大模型部署」「模型运维」表述，现有词表无对应项</p>
            <div style="margin-top:8px">
              <div class="muted" style="font-size:12px;margin-bottom:4px">触发该建议的样例档案（≤20）</div>
              <span class="chip">李伟</span><span class="chip">王强</span><span class="chip">赵敏</span>
              <span class="chip muted">+11</span>
            </div>
          </div>
          <div>
            <h5><span>审核（ADM / HR）</span><span class="muted">API-54</span></h5>
            <div class="form-row" style="margin-bottom:11px">
              <label style="flex:0 0 76px">结论</label>
              <div class="ctl"><label class="radio"><input type="radio" name="dec" checked> 通过并入库</label>
                <label class="radio"><input type="radio" name="dec"> 驳回</label></div>
            </div>
            <div class="form-row" style="margin-bottom:11px">
              <label style="flex:0 0 76px">标签码</label>
              <div class="ctl"><input type="text" value="SKILL_LLM_OPS" style="width:100%">
                <div class="form-help">入词表后的唯一码（大写 + 下划线），冲突返回 40901</div></div>
            </div>
            <div class="alert alert-info" style="margin:0 0 12px"><span class="i">◈</span>
              <div>审核通过 → 状态 <span class="mono">0 待审核 → 1 启用</span>，并写字典变更留痕。<br>
              <b>历史档案不自动回填</b> —— 回填需 HR 显式触发重解析（change_source=3），
              避免静默改写历史数据。</div></div>
            <button class="btn btn-primary">提交审核结论<span class="mark">②</span></button>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>
"""
    st = states(
        sbox('loading — 分类树与标签表骨架屏', s_loading(5)),
        sbox('empty — 词表尚未初始化',
             s_empty('标签词表还是空的', '建议先导入基础词表（技能 / 经验 / 素质 / 管理四类），再由 AI 逐步补充建议',
                     '<button class="btn btn-primary">导入基础词表</button> <button class="btn">+ 新增标签</button>')),
        sbox('conflict — 标签码冲突（code 40901）',
             '<div class="alert alert-danger" style="margin:0"><span class="i">!</span>'
             '<div><b>标签码 SKILL_LLM_OPS 已存在</b>　请更换标签码，或直接启用既有标签'
             '<code>（code 40901 / data.existing_tag_id）</code></div></div>'),
        sbox('forbidden — AIOPS / MGR / EMP 无词表维护权限',
             s_forbidden('AI 运维', '仅可管理模型与日志，标签词表属业务配置')),
    )
    leg = legend([
        '<b>对应：</b>F-3.4 / F-3.4.1 · API-50~54 · TBL-18 / TBL-19 / TBL-22',
        '<b>状态：</b>默认 / 加载 / 空 / 冲突 / 无权限',
        '<b>红线：</b>AI 不得自由造词；建议不得直接入库；非启用项不可打标；词表变更留痕可追',
        '<b>注意：</b>标签漂移会让所有按标签的统计与筛选结果不可复现 —— 这是受控词表存在的唯一理由',
    ])
    return shell('p13-tag-dict.html', 'P-13', '标签字典管理', '/ 人才档案 / 标签字典',
                 'F-3.4.1', body + st + leg, extra_css=tree_css)


# ============================================================ P-14 档案 RAG 问答
def p14():
    body = """
<div class="alert alert-warn"><span class="i">◈</span>
  <div><b>本页承载一期最密集的 5 条红线：</b>
  ① 权限裁剪（全越权直接 40301，不进流）② 脱敏出网（送出前二次 NER 拦截）
  ③ 本地回填（<b>严禁降级为明文出网</b>）④ 引用溯源（<span class="mono">citation_count=0</span> 视为缺陷）
  ⑤ 无答案兜底（<span class="mono">tokens.in=0</span>，可直接验证未调模型）。</div></div>

<div class="grid" style="grid-template-columns:minmax(0,1fr) 330px;align-items:start">
  <div>
    <div class="card">
      <div class="card-hd">
        <h3>档案问答<span class="sub">API-63 · SSE 流式</span></h3>
        <span><span class="tag tag-ok">脱敏已启用</span> <span class="tag tag-gray">session s-…20001</span></span>
      </div>
      <div class="card-bd" style="padding:0">
        <div style="padding:12px 14px;border-bottom:1px solid #e3e8ef;display:flex;gap:9px;align-items:center;flex-wrap:wrap">
          <span style="font-size:13px;color:#5b6673">提问范围：</span>
          <select><option selected>批量人才集合（3 人）</option><option>单份档案</option></select>
          <span class="chip">李伟<span class="x">×</span></span>
          <span class="chip">王强<span class="x">×</span></span>
          <span class="chip">赵敏<span class="x">×</span></span>
          <span class="grow" style="flex:1"></span>
          <span class="muted" style="font-size:12px">送模型片段 5 条 · 上限 10</span>
        </div>

        <div class="chat" style="border:0;border-radius:0">
          <div class="msg user">
            <span class="av">张</span>
            <div class="bub">这批人才里谁最适合做数据治理？依据是什么？</div>
          </div>

          <div class="msg ai">
            <span class="av">AI</span>
            <div class="bub">
              <p>在这批人才中，<b>李伟</b>（数据治理部）最匹配。其依据为：</p>
              <p>① 主导集团主数据平台建设，覆盖 12 个业务域，负责数据标准与主数据管理；</p>
              <p>② 技能项中「数据治理」为精通级，「主数据管理」为熟练级；</p>
              <p>③ 持有 CDGA / CDGP 两项数据治理领域专业认证。</p>
              <p>其余两位（王强、赵敏）在数据治理方向的档案依据不足，不满足追问条件。</p>

              <div class="cite">
                <div class="hd"><span>引用 1 · 项目经历</span><a href="p08-talent-detail.html">李伟 →</a></div>
                <div class="txt">主导集团主数据平台建设，覆盖 12 个业务域，负责数据标准与主数据管理</div>
              </div>
              <div class="cite">
                <div class="hd"><span>引用 2 · 技能</span><a href="p08-talent-detail.html">李伟 →</a></div>
                <div class="txt">数据治理（精通）、主数据管理（熟练）</div>
              </div>

              <div class="meta">
                模型 cloud-llm-a · tokens 1832/412 · ¥0.0421 · 耗时 2.87s ·
                <span class="tag tag-ok">mask_applied=true</span>
                <span class="tag tag-gray">citation_count=2</span>
              </div>
            </div>
          </div>

          <div class="msg user">
            <span class="av">张</span>
            <div class="bub">他的短板是什么？</div>
          </div>

          <div class="msg ai" style="margin-bottom:0">
            <span class="av">AI</span>
            <div class="bub">
              <p style="color:#98a2ae">正在生成…<span class="pbadge" style="margin-left:6px">流式中</span></p>
              <p>从档案看，其工作经历集中在数据治理与主数据方向，<b>缺乏跨部门协作与团队管理</b>的明确记载；
              项目角色多为技术负责人，未见预算或人员规模相关的描述。</p>
              <div class="meta">event: delta 已回填真值 · event: ping 15s 心跳</div>
            </div>
          </div>
        </div>

        <div style="padding:12px 14px;border-top:1px solid #e3e8ef">
          <div class="compose">
            <textarea rows="2" placeholder="继续追问，例如：他与王强相比优势在哪？"></textarea>
            <button class="btn btn-primary" style="height:auto;padding:9px 16px">发送<span class="mark">①</span></button>
          </div>
          <div class="form-help">支持 ≥10 轮上下文；指代消解（「他」→ 李伟）由本地 qwen2.5:3b 完成，改写后再检索。</div>
        </div>
      </div>
    </div>
  </div>

  <div>
    <div class="card">
      <div class="card-hd"><h3>脱敏与回填链路<span class="sub">F-3.9 / D-15</span></h3></div>
      <div class="card-bd">
        <table style="font-size:12.5px">
          <tbody>
            <tr><td>检索单元入库</td><td class="num"><span class="tag tag-ok">已脱敏</span></td></tr>
            <tr><td>出网前二次 NER</td><td class="num"><span class="tag tag-ok">通过</span></td></tr>
            <tr><td>占位符本地回填</td><td class="num"><span class="tag tag-ok">服务端完成</span></td></tr>
            <tr><td>是否明文出网</td><td class="num"><span class="tag tag-ok">否</span></td></tr>
          </tbody>
        </table>
        <div style="margin-top:12px;font-size:12.5px;color:#5b6673;line-height:1.95">
          <div style="font-weight:600;color:#1c2430;margin-bottom:4px">占位符对照（模型看到 / 用户看到）</div>
          姓名 → <span class="ph">{{CANDIDATE}}</span>　→　李伟<br>
          手机 → <span class="ph">{{PHONE}}</span>　→　13812348888<br>
          身份证 → <span class="ph">{{ID}}</span>　→　3301…1234<br>
          邮箱 → <span class="ph">{{EMAIL}}</span>　→　liwei@example.com
        </div>
        <div class="alert alert-info" style="margin:12px 0 0"><span class="i">◈</span>
          <div>向量库内<b>不得存在明文身份信息</b>；回填<b>不经过任何外部服务</b>；
          回填失败 → 拒绝作答，<b>绝不降级为明文出网</b>。</div></div>
      </div>
    </div>

    <div class="card">
      <div class="card-hd"><h3>本次范围裁剪<span class="sub">权限前置</span></h3></div>
      <div class="card-bd">
        <table style="font-size:12.5px">
          <tbody>
            <tr><td>请求范围</td><td class="num">3 份</td></tr>
            <tr><td>权限允许</td><td class="num">2 份</td></tr>
            <tr><td>已裁剪</td><td class="num"><span class="tag tag-warn">clipped=true</span></td></tr>
          </tbody>
        </table>
        <div class="alert alert-warn" style="margin:12px 0 0"><span class="i">◈</span>
          <div>已按您的权限裁剪范围：赵敏（技术研发中心）不在您的数据域内，未参与检索。</div></div>
        <div class="form-help" style="margin-top:9px">
          <span class="mono">meta.scope_effective = {requested:3, allowed:2, clipped:true}</span><br>
          若全部越权 → 直接 40301，<b>不进流</b>。
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-hd"><h3>会话</h3>
        <button class="btn btn-sm">清空</button></div>
      <table style="font-size:12.5px">
        <tbody>
          <tr><td>s-…20001</td><td>批量问答 · 3 人</td><td class="muted">今天 16:41</td></tr>
          <tr><td>s-…19904</td><td>单档问答 · 李伟</td><td class="muted">今天 14:31</td></tr>
          <tr><td>s-…19776</td><td>批量问答 · 12 人</td><td class="muted">昨天 17:05</td></tr>
        </tbody>
      </table>
      <div class="legend" style="padding:10px 13px;margin:0;border-top:1px solid #e3e8ef">
        <b>断线策略：</b>问答不自动重连（避免重复计费与重复引用）；<br>
        断线后可用会话历史取回已生成内容。
      </div>
    </div>
  </div>
</div>
"""
    st = states(
        sbox('loading — 首帧 event: meta 前的等待态（禁用发送按钮）',
             s_loading(3) + '<div class="form-help">SSE 首帧 meta 到达前不展示任何增量文本</div>'),
        sbox('refusal — 无答案兜底（LOW_SIMILARITY）：不调用大模型',
             '<div class="chat" style="min-height:auto"><div class="msg ai" style="margin-bottom:0">'
             '<span class="av">AI</span><div class="bub">'
             '<p>档案中未找到与该问题相关的依据，无法作答。</p>'
             '<div class="meta">event: refusal reason=LOW_SIMILARITY · '
             '<span class="tag tag-warn">tokens.in = 0</span> '
             '<span class="tag tag-gray">fallback=true</span></div>'
             '</div></div></div>'
             '<div class="form-help">相似度低于阈值 → 直接兜底，<b>不调用大模型</b>。'
             '<span class="mono">tokens.in=0</span> 是可直接验证的凭据。前端按<b>普通回答</b>渲染，不弹错误。</div>'),
        sbox('refusal — 脱敏拦截（NER_RESIDUAL）：绝不降级为明文出网',
             '<div class="chat" style="min-height:auto"><div class="msg ai" style="margin-bottom:0">'
             '<span class="av">AI</span><div class="bub">'
             '<p>该内容涉及敏感信息，无法生成回答。</p>'
             '<div class="meta">event: refusal reason=NER_RESIDUAL · blocked=true · '
             '<b>未发生明文出网</b></div></div></div></div>'
             '<div class="form-help">送出模型前二次 NER 扫描发现残留明文身份 → 拦截并记日志。'
             '<b>这是唯一正确的处置</b>：宁可拒答，不可降级出网。</div>'),
        sbox('error — 全部范围越权（不进流，直接 40301）',
             '<div class="alert alert-danger" style="margin:0"><span class="i">🔒</span>'
             '<div><b>提问范围包含无权访问的档案</b><br>'
             'denied_talent_ids: [1839204770000000200]<br>'
             '注意：全部越权时<b>直接拒绝，不进入流式</b> —— 避免把无权数据送进模型上下文。</div></div>'),
        sbox('forbidden — AIOPS / AUDIT 无问答权限',
             s_forbidden('审计员', '仅可只读审计日志与变更留痕，不可发起档案问答')),
    )
    leg = legend([
        '<b>对应：</b>F-3.7 / F-3.9 · API-63 · COL-01~03 / TBL-11 / TBL-24',
        '<b>状态：</b>默认 / 首帧等待 / 流式中 / 兜底 / 脱敏拦截 / 全越权 / 无权限',
        '<b>SSE 事件：</b>meta / delta / citation / refusal / done / error（+ ping 心跳）',
        '<b>红线：</b>5 条缺一不可 —— 权限裁剪 · 脱敏出网 · 本地回填 · 引用溯源 · 无答案兜底',
    ])
    return shell('p14-talent-ask.html', 'P-14', '档案 RAG 问答', '/ 人才档案 / 档案问答',
                 'F-3.7 / F-3.9', body + st + leg)


# ============================================================ P-15 我的画像
def p15():
    body = """
<div class="alert alert-info"><span class="i">◈</span>
  <div>您是<b>本人视角</b>（data_scope=3 仅本人）：只能看到自己的档案，且可对自己的数据行使
  <b>查阅 / 更正 / 删除 / 导出</b> 四项权利（F-3.11，《个人信息保护法》）。全部操作留痕。</div></div>

<div class="grid" style="grid-template-columns:minmax(0,1fr) 336px;align-items:start">
  <div>
    <div class="card">
      <div class="card-hd"><h3>我的档案<span class="sub">来源标注 · 可追溯</span></h3>
        <span class="tag tag-ok">仅本人可见</span></div>
      <div class="card-bd">
        <div class="grid g4" style="gap:12px">
          <div><div class="muted" style="font-size:12px">姓名</div><div>李伟</div></div>
          <div><div class="muted" style="font-size:12px">学历</div><div>硕士</div></div>
          <div><div class="muted" style="font-size:12px">院校</div><div>某某大学</div></div>
          <div><div class="muted" style="font-size:12px">工作年限</div><div>6 年</div></div>
        </div>
        <table style="margin-top:14px">
          <thead><tr><th>字段</th><th>当前值</th><th>来源</th><th>更新时间</th><th>操作</th></tr></thead>
          <tbody>
            <tr><td>工作年限</td><td><b>6 年</b></td><td><span class="tag tag-warn">人工复核</span></td>
              <td class="muted">09-21 16:22</td><td class="ops"><a href="#corr">申请更正<span class="mark">①</span></a></td></tr>
            <tr><td>技能</td><td>数据治理、主数据管理、SQL…</td><td><span class="tag tag-info">AI 解析</span></td>
              <td class="muted">09-21 14:20</td><td class="ops"><a href="#corr">申请更正</a></td></tr>
            <tr><td>项目经历</td><td>集团主数据平台 等 3 项</td><td><span class="tag tag-info">AI 解析</span></td>
              <td class="muted">07-14 09:31</td><td class="ops"><a href="#corr">申请更正</a></td></tr>
            <tr><td>电话</td><td class="mono">138****8888</td><td><span class="tag tag-gray">原始录入</span></td>
              <td class="muted">03-02 10:11</td><td class="ops"><a href="#corr">申请更正</a></td></tr>
          </tbody>
        </table>
        <div class="form-help" style="margin-top:10px">
          「来源」分为 AI 解析 / 人工复核 / 合并 / 重解析四类（change_source 0~3）——
          这是您判断某个字段是否可信的依据。
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-hd"><h3>我的数字画像<span class="sub">F-3.4</span></h3></div>
      <div class="card-bd" style="padding:6px 0 0">
""" + RADAR + """
        <div style="padding:0 14px 14px">
          <div class="muted" style="font-size:12px;margin-bottom:6px">画像标签</div>
          <span class="chip">数据治理</span><span class="chip">AI 项目经验</span><span class="chip">主数据管理</span>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-hd"><h3>申请更正<span class="sub">API-72 · 复用 F-3.3 人工复核队列</span></h3></div>
      <div class="card-bd">
        <div class="form-row"><label><span class="req">*</span>更正字段</label>
          <div class="ctl"><select style="width:220px"><option selected>工作年限</option><option>技能</option><option>院校</option></select></div></div>
        <div class="form-row"><label>当前值</label>
          <div class="ctl"><input type="text" value="6" style="width:220px" readonly>
            <div class="form-help">服务端会校验是否与库中一致 —— 不一致返回 40902（防覆盖 HR 刚修好的值）</div></div></div>
        <div class="form-row"><label><span class="req">*</span>更正为</label>
          <div class="ctl"><input type="text" value="7" style="width:220px"></div></div>
        <div class="form-row"><label><span class="req">*</span>更正理由</label>
          <div class="ctl"><textarea rows="3" style="width:100%" placeholder="请说明理由，10~500 字符">补充了 2024 年新增的一段数据治理工作经历，实际累计 7 年。</textarea></div></div>
        <div class="form-row"><label>佐证材料</label>
          <div class="ctl"><button class="btn btn-sm">+ 上传（≤5 个）</button>
            <div class="form-help">已上传：任职证明.pdf</div></div></div>
        <div class="form-row"><label></label>
          <div class="ctl"><button class="btn btn-primary">提交更正申请<span class="mark">②</span></button>
            <span class="muted" style="font-size:12.5px;margin-left:10px">处理时限 72 小时 · 结果将通知您</span></div></div>
      </div>
    </div>
  </div>

  <div>
    <div class="card">
      <div class="card-hd"><h3>我的权利台账<span class="sub">F-3.11</span></h3></div>
      <div class="card-bd">
        <table style="font-size:12.5px">
          <thead><tr><th>权利</th><th>状态</th><th>时间</th></tr></thead>
          <tbody>
            <tr><td>查阅</td><td><span class="tag tag-ok">已响应</span></td><td class="muted">今天 16:40</td></tr>
            <tr><td>更正</td><td><span class="tag tag-info">处理中</span></td><td class="muted">今天 16:41</td></tr>
            <tr><td>删除</td><td><span class="tag tag-gray">未申请</span></td><td class="muted">—</td></tr>
            <tr><td>导出</td><td><span class="tag tag-ok">已生成</span></td><td class="muted">09-18 10:02</td></tr>
          </tbody>
        </table>
        <div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap">
          <button class="btn btn-sm">申请删除<span class="mark">③</span></button>
          <button class="btn btn-sm">申请导出</button>
          <button class="btn btn-sm">下载数据包</button>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-hd"><h3>删除申请说明<span class="sub">高风险</span></h3></div>
      <div class="card-bd" style="font-size:12.5px;color:#5b6673;line-height:1.95">
        ① 需 <b>ADM 审批</b>（HR 仅可处理更正，不可审批删除）；<br>
        ② 执行方式：<b>软删除</b>（保留留痕，物理删除按留存策略）；<br>
        ③ <b>Milvus 向量必须同步清理</b>，避免检索到已删档案；<br>
        ④ 向量清理失败 → <b>回滚软删</b>，申请保持待处理。<br>
        <span style="color:#9c3434">绝不允许「库删了、向量没删」—— 既是权利响应失效，也是数据泄露。</span>
      </div>
    </div>

    <div class="card">
      <div class="card-hd"><h3>导出记录</h3></div>
      <table style="font-size:12.5px">
        <tbody>
          <tr><td>2026-09-18</td><td><span class="tag tag-ok">已下载</span></td></tr>
          <tr><td>2026-06-02</td><td><span class="tag tag-gray">已过期</span></td></tr>
        </tbody>
      </table>
      <div class="legend" style="padding:10px 13px;margin:0;border-top:1px solid #e3e8ef">
        数据包格式：JSON + PDF（API-78）<br>下载行为单独留痕（TBL-28）
      </div>
    </div>
  </div>
</div>
"""
    st = states(
        sbox('loading — 本人档案与画像骨架屏', s_loading(5)),
        sbox('pending — 更正申请已提交，等待 HR 处理',
             '<div class="alert alert-info" style="margin:0"><span class="i">◈</span>'
             '<div>更正申请已提交（request_id …30001），进入字段复核队列，处理时限 72 小时。<br>'
             '处理结果将通过站内信通知您。处理期间档案字段保持原值。</div></div>'),
        sbox('error — 乐观锁冲突（code 40902）：当前值已被变更',
             '<div class="alert alert-warn" style="margin:0"><span class="i">!</span>'
             '<div><b>该字段当前值已变更，请刷新后重新提交</b><br>'
             'latest_value: 7　latest_updated_at: 2026-09-21 15:58:02<code>（code 40902）</code></div></div>'),
        sbox('forbidden — 管理者不可访问本人权利入口',
             s_forbidden('HR 管理员', '「我的画像 / 个人权利」仅普通员工（EMP）本人可用；管理者走档案详情页')),
    )
    leg = legend([
        '<b>对应：</b>F-3.4 / F-3.11 · API-34 / API-71~79 · TBL-11~16 / TBL-22 / TBL-28 / COL-01~03',
        '<b>状态：</b>默认 / 加载 / 申请中 / 乐观锁冲突 / 无权限',
        '<b>红线：</b>仅本人可访问；四项权利全留痕；删除 = 软删 + 向量同步清理，失败即回滚',
        '<b>注意：</b>API-71/72/75/78 在权限矩阵中仅 EMP（本人），管理者无此接口权限',
    ])
    return shell('p15-my-profile.html', 'P-15', '我的画像', '/ 我的画像',
                 'F-3.4 / F-3.11', body + st + leg)


# ============================================================ P-16 模型与 Prompt
def p16():
    body = """
<div class="alert alert-warn"><span class="i">◈</span>
  <div><b>混合算力路由（D-4）：</b>含个人隐私与高吞吐任务走<b>本地</b>（不出网）；
  仅「档案问答生成」与「Agent 复杂推理」走<b>云端</b>，且<b>必须基于脱敏后的占位符片段</b>。<br>
  本页仅 AIOPS 可访问，且<b>不含任何业务数据明文</b> —— 只做模型与 Prompt 配置。</div></div>

<div class="toolbar">
  <select><option selected>全部用途</option><option>本地推理</option><option>云端生成</option><option>向量化</option></select>
  <button class="btn">刷新健康状态</button>
  <span class="grow"></span>
  <button class="btn">查看切换告警</button>
  <button class="btn btn-primary">+ 新增模型配置</button>
</div>

<div class="card">
  <div class="card-hd"><h3>模型配置与路由<span class="sub">TBL-23 · F-9.1</span></h3>
    <span class="muted" style="font-size:12px">主模型失败自动切备用，切换耗时 ≤5s 并告警</span></div>
  <table>
    <thead><tr><th>用途</th><th>模型</th><th>部署</th><th>出网</th><th>主/备</th>
      <th>健康</th><th>P95 延迟</th><th>操作</th></tr></thead>
    <tbody>
      <tr><td>简历字段抽取</td><td class="mono">qwen2.5:3b</td><td><span class="tag tag-gray">本地 Ollama</span></td>
        <td><span class="tag tag-ok">否</span></td><td><span class="tag tag-info">主</span></td>
        <td><span class="tag tag-ok">正常</span></td><td class="num">1.9s</td>
        <td class="ops"><a href="#">编辑</a><a href="#">切换主备</a></td></tr>
      <tr><td>Agent 复杂推理</td><td class="mono">qwen3:8b</td><td><span class="tag tag-gray">本地 Ollama</span></td>
        <td><span class="tag tag-ok">否</span></td><td><span class="tag tag-gray">备</span></td>
        <td><span class="tag tag-ok">正常</span></td><td class="num">4.4s</td>
        <td class="ops"><a href="#">编辑</a><a href="#">切换主备</a></td></tr>
      <tr><td>文本向量化</td><td class="mono">bge-m3</td><td><span class="tag tag-gray">本地</span></td>
        <td><span class="tag tag-ok">否</span></td><td><span class="tag tag-info">主</span></td>
        <td><span class="tag tag-ok">正常</span></td><td class="num">38ms</td>
        <td class="ops"><a href="#">编辑</a></td></tr>
      <tr><td>Rerank 精排</td><td class="mono">bge-reranker-v2-m3</td><td><span class="tag tag-gray">本地</span></td>
        <td><span class="tag tag-ok">否</span></td><td><span class="tag tag-info">主</span></td>
        <td><span class="tag tag-ok">正常</span></td><td class="num">96ms</td>
        <td class="ops"><a href="#">编辑</a></td></tr>
      <tr><td>档案问答生成</td><td class="mono">cloud-llm-a</td>
        <td><span class="tag tag-warn">云端</span></td>
        <td><span class="tag tag-danger">是（脱敏后）</span></td><td><span class="tag tag-info">主</span></td>
        <td><span class="tag tag-warn">降级中</span></td><td class="num">2.1s</td>
        <td class="ops"><a href="#">编辑</a><a href="#">切回主模型</a></td></tr>
      <tr><td>档案问答生成</td><td class="mono">cloud-llm-b</td>
        <td><span class="tag tag-warn">云端</span></td>
        <td><span class="tag tag-danger">是（脱敏后）</span></td><td><span class="tag tag-ok">备用生效</span></td>
        <td><span class="tag tag-ok">正常</span></td><td class="num">1.7s</td>
        <td class="ops"><a href="#">编辑</a></td></tr>
      <tr><td>问题改写 / 意图识别</td><td class="mono">qwen2.5:3b</td><td><span class="tag tag-gray">本地</span></td>
        <td><span class="tag tag-ok">否</span></td><td><span class="tag tag-info">主</span></td>
        <td><span class="tag tag-ok">正常</span></td><td class="num">220ms</td>
        <td class="ops"><a href="#">编辑</a></td></tr>
      <tr><td>出网前 NER 脱敏</td><td class="mono">本地 NER + 规则库</td><td><span class="tag tag-gray">本地</span></td>
        <td><span class="tag tag-ok">否</span></td><td><span class="tag tag-info">主</span></td>
        <td><span class="tag tag-ok">正常</span></td><td class="num">64ms</td>
        <td class="ops"><a href="#">编辑</a></td></tr>
    </tbody>
  </table>
</div>

<div class="card">
  <div class="card-hd"><h3>Prompt 模板<span class="sub">TBL-23 · F-9.2 · 版本化 + 回滚</span></h3>
    <button class="btn btn-sm btn-primary">+ 新建</button></div>
  <table>
    <thead><tr><th>Prompt 编码</th><th>用途</th><th>当前版本</th><th>最近变更</th><th>使用次数</th><th>操作</th></tr></thead>
    <tbody>
      <tr><td class="mono">ASK_TALENT</td><td>档案 RAG 问答</td><td><span class="tag tag-ok">v3</span></td>
        <td class="muted">09-20 14:02 · 王强</td><td class="num">3,412</td>
        <td class="ops"><a href="#pv">版本对比<span class="mark">①</span></a><a href="#">回滚</a></td></tr>
      <tr><td class="mono">PARSE_RESUME</td><td>简历字段抽取</td><td><span class="tag tag-ok">v7</span></td>
        <td class="muted">09-14 10:31 · 李工</td><td class="num">18,904</td>
        <td class="ops"><a href="#pv">版本对比</a><a href="#">回滚</a></td></tr>
      <tr><td class="mono">QUERY_REWRITE</td><td>问题改写 / 指代消解</td><td><span class="tag tag-ok">v2</span></td>
        <td class="muted">08-28 16:47 · 李工</td><td class="num">6,120</td>
        <td class="ops"><a href="#pv">版本对比</a><a href="#">回滚</a></td></tr>
      <tr><td class="mono">TAG_SUGGEST</td><td>标签建议（入待审核）</td><td><span class="tag tag-gray">v1 草稿</span></td>
        <td class="muted">09-21 11:20 · 张敏</td><td class="num">42</td>
        <td class="ops"><a href="#">编辑</a><a href="#">启用</a></td></tr>
    </tbody>
  </table>
</div>

<div class="card" id="pv">
  <div class="card-hd"><h3>版本对比 · ASK_TALENT<span class="sub">v2 → v3</span></h3>
    <button class="btn btn-sm">回滚到 v2</button></div>
  <div class="card-bd">
    <div class="diff">
      <div><h5><span>v2（2026-08-11）</span><span class="muted">已停用</span></h5>
        <p>你是人才档案分析助手。请依据提供的档案片段回答问题。</p>
        <p class="muted">要求：回答简洁，不超过 200 字。</p></div>
      <div><h5><span>v3（2026-09-20 · 当前）</span><span class="tag tag-ok">启用中</span></h5>
        <p>你是人才档案分析助手。请<b>仅依据</b>提供的档案片段回答问题。</p>
        <p class="hl-ok">每条结论必须标注引用序号，格式如「…（引用 1）」。<br>
        若片段中无相关依据，必须回答「档案中未找到依据」，<b>不得推测或补全</b>。</p>
        <p class="muted">片段中的 <span class="ph">{{CANDIDATE}}</span> 等占位符请按原样保留，不要改写。</p></div>
    </div>
    <div class="alert alert-info" style="margin:14px 0 0"><span class="i">◈</span>
      <div>每次调用记录所用 <span class="mono">prompt_code + prompt_version</span>（落 TBL-24），
      保证任何一次回答都能回溯到当时的确切 Prompt —— 这是"回答质量变化"能被归因的前提。</div></div>
  </div>
</div>
"""
    st = states(
        sbox('loading — 模型表与 Prompt 表骨架屏', s_loading(5)),
        sbox('alert — 主模型熔断，备用模型已接管',
             '<div class="alert alert-warn" style="margin:0"><span class="i">◈</span>'
             '<div><b>cloud-llm-a 连续 3 次超时，已自动切换至 cloud-llm-b</b><br>'
             '切换耗时 3.2s（要求 ≤5s）· 切换事件已告警 · 原模型恢复后需手工切回或按策略自动切回</div></div>'),
        sbox('error — 模型全部不可用（降级返回兜底，不抛 500）',
             '<div class="alert alert-danger" style="margin:0"><span class="i">!</span>'
             '<div><b>云端生成模型全部不可用</b><br>'
             '业务侧将返回 HTTP 200 + <span class="mono">code=0, degraded=true</span> + 兜底文案，'
             '<b>不抛 500</b> —— 否则前端无法区分业务失败与服务降级。</div></div>'),
        sbox('forbidden — 非 AIOPS 不可访问模型与 Prompt 配置',
             s_forbidden('HR 管理员', '不可访问模型与 Prompt 配置；如需查看调用成本请走「调用日志与成本看板」')),
    )
    leg = legend([
        '<b>对应：</b>F-9.1 / F-9.2 / F-1.6 · API-81~87 · TBL-23',
        '<b>状态：</b>默认 / 加载 / 熔断告警 / 全不可用 / 无权限',
        '<b>红线：</b>Prompt 版本化（改 = 生成新版本，不改历史）；每次调用记录版本号；云端调用必须脱敏',
        '<b>注意：</b>AIOPS 看不到任何业务数据明文 —— 本页不含档案内容',
    ])
    return shell('p16-ai-model.html', 'P-16', '模型配置与 Prompt 管理', '/ AI 中台 / 模型与 Prompt',
                 'F-9.1 / F-9.2', body + st + leg)


# ============================================================ P-17 AI 调用日志与成本
COSTCHART = """
<div class="chart"><svg viewBox="0 0 560 200" width="560" height="200" role="img" aria-label="近 7 日调用成本趋势">
  <g stroke="#eef2f7" stroke-width="1">
    <line x1="30" y1="170" x2="540" y2="170"/>
    <line x1="30" y1="100" x2="540" y2="100"/>
    <line x1="30" y1="30" x2="540" y2="30"/>
  </g>
  <g fill="#1b5fd9" fill-opacity="0.82">
    <rect x="42" y="100" width="40" height="70" rx="2"/>
    <rect x="104" y="57" width="40" height="113" rx="2"/>
    <rect x="166" y="77" width="40" height="93" rx="2"/>
    <rect x="228" y="30" width="40" height="140" rx="2"/>
    <rect x="290" y="40" width="40" height="130" rx="2"/>
    <rect x="352" y="83" width="40" height="87" rx="2"/>
    <rect x="414" y="61" width="40" height="109" rx="2"/>
  </g>
  <rect x="414" y="61" width="40" height="109" rx="2" fill="#b7791f" fill-opacity="0.85"/>
  <g fill="#98a2ae" font-size="11" font-family="-apple-system,Microsoft YaHei,sans-serif" text-anchor="middle">
    <text x="62" y="186">09-15</text><text x="124" y="186">09-16</text><text x="186" y="186">09-17</text>
    <text x="248" y="186">09-18</text><text x="310" y="186">09-19</text><text x="372" y="186">09-20</text>
    <text x="434" y="186">09-21</text>
    <text x="24" y="174" text-anchor="end">0</text>
    <text x="24" y="104" text-anchor="end">2</text>
    <text x="24" y="34" text-anchor="end">4</text>
  </g>
  <g fill="#5b6673" font-size="11" font-family="-apple-system,Microsoft YaHei,sans-serif" text-anchor="middle">
    <text x="62" y="94">2.10</text><text x="124" y="51">3.40</text><text x="186" y="71">2.80</text>
    <text x="248" y="24">4.20</text><text x="310" y="34">3.90</text><text x="372" y="77">2.60</text>
    <text x="434" y="55" text-anchor="middle" fill="#b7791f" font-weight="700">3.28</text>
  </g>
</svg></div>
"""


def p17():
    body = """
<div class="grid g4" style="margin-bottom:14px">
  <div class="stat"><div class="lbl">今日调用</div><div class="num">1,743<small>次</small></div>
    <div class="sub">日均 5,000 上限内</div></div>
  <div class="stat"><div class="lbl">今日成本</div><div class="num">¥ 3.28</div>
    <div class="sub">近 7 日峰值 4.20</div></div>
  <div class="stat"><div class="lbl">脱敏出网占比<span class="mark">①</span></div>
    <div class="num" style="color:#1f9254">100<small>%</small></div>
    <div class="sub">cloud 场景必须恒为 100%</div></div>
  <div class="stat"><div class="lbl">缓存命中率</div><div class="num">11.4<small>%</small></div>
    <div class="sub">命中即 0 成本</div></div>
</div>

<div class="alert alert-ok"><span class="i">✓</span>
  <div><b>合规核查通过：</b><span class="mono">mask_applied_rate = 1.0</span>，即所有云端调用均经脱敏出网。
  该指标低于 1.0 即视为存在未脱敏出网调用，会立刻告警。</div></div>

<div class="grid" style="grid-template-columns:minmax(0,1fr) 430px;align-items:start">
  <div>
    <div class="card">
      <div class="card-hd"><h3>调用明细<span class="sub">TBL-24 · 按月分区，查询须带时间范围</span></h3></div>
      <table>
        <thead><tr><th>时间</th><th>场景</th><th>模型</th><th class="num">in/out</th>
          <th class="num">成本</th><th class="num">耗时</th><th>脱敏</th><th>结果</th></tr></thead>
        <tbody>
          <tr><td class="muted nowrap">14:31:02</td><td>档案问答</td><td class="mono">cloud-llm-a</td>
            <td class="num">1832/412</td><td class="num">¥0.0421</td><td class="num">2871ms</td>
            <td><span class="tag tag-ok">是</span></td><td><span class="tag tag-ok">成功</span></td></tr>
          <tr><td class="muted nowrap">14:28:44</td><td>问题改写</td><td class="mono">qwen2.5:3b</td>
            <td class="num">212/38</td><td class="num">¥0.0000</td><td class="num">220ms</td>
            <td><span class="tag tag-gray">本地不出网</span></td><td><span class="tag tag-ok">成功</span></td></tr>
          <tr><td class="muted nowrap">14:22:07</td><td>简历解析</td><td class="mono">qwen2.5:3b</td>
            <td class="num">3418/902</td><td class="num">¥0.0000</td><td class="num">3.1s</td>
            <td><span class="tag tag-gray">本地不出网</span></td><td><span class="tag tag-ok">成功</span></td></tr>
          <tr><td class="muted nowrap">14:21:33</td><td>向量化</td><td class="mono">bge-m3</td>
            <td class="num">—/1024d</td><td class="num">¥0.0000</td><td class="num">38ms</td>
            <td><span class="tag tag-gray">本地不出网</span></td><td><span class="tag tag-ok">成功</span></td></tr>
          <tr><td class="muted nowrap">13:58:12</td><td>档案问答</td><td class="mono">cloud-llm-b</td>
            <td class="num">2104/388</td><td class="num">¥0.0389</td><td class="num">1.7s</td>
            <td><span class="tag tag-ok">是</span></td><td><span class="tag tag-ok">成功</span></td></tr>
          <tr><td class="muted nowrap">13:40:02</td><td>档案问答</td><td class="mono">cloud-llm-a</td>
            <td class="num">0/0</td><td class="num">¥0.0000</td><td class="num">412ms</td>
            <td><span class="tag tag-ok">是</span></td><td><span class="tag tag-warn">兜底未调模型</span></td></tr>
          <tr><td class="muted nowrap">11:07:55</td><td>档案问答</td><td class="mono">cloud-llm-a</td>
            <td class="num">1955/—</td><td class="num">¥0.0121</td><td class="num">30s</td>
            <td><span class="tag tag-ok">是</span></td><td><span class="tag tag-danger">超时 50401</span></td></tr>
        </tbody>
      </table>
      <div class="pager"><span>共 174,382 条（近 7 天）　每页 20 条</span>
        <div class="pages"><span>‹</span><span class="cur">1</span><span>2</span><span>3</span><span>…</span><span>8720</span><span>›</span></div></div>
    </div>
  </div>

  <div>
    <div class="card">
      <div class="card-hd"><h3>近 7 日成本趋势<span class="sub">单位：元</span></h3></div>
      <div class="card-bd" style="padding:8px 6px">
""" + COSTCHART + """
        <div style="padding:0 8px 6px;font-size:12px;color:#98a2ae">
          金色柱为今日（09-21）。成本超阈值会写入告警，本页只呈报不告警。
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-hd"><h3>按场景汇总<span class="sub">近 7 天</span></h3></div>
      <table style="font-size:12.5px">
        <thead><tr><th>场景</th><th class="num">调用</th><th class="num">成本</th><th class="num">平均耗时</th></tr></thead>
        <tbody>
          <tr><td>档案问答（云端）</td><td class="num">1,743</td><td class="num">¥31.4288</td><td class="num">1902ms</td></tr>
          <tr><td>简历解析（本地）</td><td class="num">18,904</td><td class="num">¥0.0000</td><td class="num">3.1s</td></tr>
          <tr><td>向量化（本地）</td><td class="num">62,418</td><td class="num">¥0.0000</td><td class="num">38ms</td></tr>
          <tr><td>问题改写（本地）</td><td class="num">6,120</td><td class="num">¥0.0000</td><td class="num">220ms</td></tr>
        </tbody>
      </table>
      <div class="legend" style="padding:10px 13px;margin:0;border-top:1px solid #e3e8ef">
        <b>成本只看云端</b>：本地模型（Ollama / bge-m3 / Reranker）无 API 计费<br>
        <b>本页不含业务数据明文</b>（问题原文属业务数据，AIOPS 不可见）
      </div>
    </div>
  </div>
</div>
"""
    st = states(
        sbox('loading — KPI 与表格骨架屏', s_loading(5)),
        sbox('empty — 所选时间段无调用记录',
             s_empty('该时间段没有 AI 调用记录', '请调整时间范围（默认最近 7 天，最长 90 天）')),
        sbox('error — 查询区间超限（code 40001）',
             '<div class="alert alert-danger" style="margin:0"><span class="i">!</span>'
             '<div><b>查询区间不得超过 90 天</b>（请求 180 天）<br>'
             '日志按月分区，全分区扫描会拖垮库 —— 这是硬限制，不是性能建议。</div></div>'),
        sbox('alert — 脱敏覆盖率异常（必须立即排查）',
             '<div class="alert alert-danger" style="margin:0"><span class="i">!</span>'
             '<div><b>mask_applied_rate = 0.92 &lt; 1.0</b><br>'
             '存在未标记脱敏的 cloud 场景调用 —— 可能意味着<b>明文出网</b>。'
             '请立即按 trace_id 逐条核查。</div></div>'),
        sbox('forbidden — HR / MGR / EMP 不可访问',
             s_forbidden('HR 管理员', '不可查看 AI 调用日志与成本看板（仅 ADM / AIOPS / AUDIT 可读）')),
    )
    leg = legend([
        '<b>对应：</b>F-9.5 · API-88 / API-89 · TBL-24',
        '<b>状态：</b>默认 / 加载 / 空 / 区间超限 / 脱敏异常 / 无权限',
        '<b>红线：</b>mask_applied 是合规核查的唯一抓手；cloud 场景必须恒为 true；低于 1.0 即告警',
        '<b>注意：</b>本页不含问题原文（属业务数据）；AUDIT 只读不可改',
    ])
    return shell('p17-ai-calllog.html', 'P-17', 'AI 调用日志与成本看板', '/ AI 中台 / 调用日志',
                 'F-9.5', body + st + leg)


# ============================================================ P-18 Agent 任务与人工介入待办
def p18():
    body = """
<div class="alert alert-warn"><span class="i">◈</span>
  <div><b>状态与记忆的职责边界（M-2 处置）：</b>
  Agent 权威状态落 <b>MySQL</b>（<span class="mono">agent_task.graph_state</span>，不可丢）；
  Redis 只存<b>可丢</b>的短期热记忆（会话上下文，TTL 2h）。<br>
  <b>判定口诀：</b>丢了会不会做错？会 → 落 MySQL；不会 → 放 Redis。</div></div>

<div class="grid g4" style="margin-bottom:14px">
  <div class="stat"><div class="lbl">运行中任务</div><div class="num">3<small>个</small></div>
    <div class="sub">解析 2 · 问答 1</div></div>
  <div class="stat"><div class="lbl">待人工介入<span class="mark">①</span></div><div class="num up">2<small>项</small></div>
    <div class="sub">档案合并 1 · 高成本查询 1</div></div>
  <div class="stat"><div class="lbl">失败待重试</div><div class="num">1<small>个</small></div>
    <div class="sub">重试 3 次耗尽，进人工队列</div></div>
  <div class="stat"><div class="lbl">今日完成</div><div class="num">26<small>个</small></div>
    <div class="sub">成功率 96.3%</div></div>
</div>

<div class="card">
  <div class="card-hd"><h3>人工介入待办<span class="sub">TBL-27 · F-A0.3 · API-93</span></h3>
    <span class="muted" style="font-size:12px">关键决策点必须人工确认：测评结论 · 能力评级 · 档案合并 · 高成本查询</span></div>
  <table>
    <thead><tr><th>待办 ID</th><th>类型</th><th>关联任务</th><th>触发原因</th>
      <th>等待时长</th><th>指派处理人</th><th>操作</th></tr></thead>
    <tbody>
      <tr><td class="mono">…50001</td><td><span class="tag tag-warn">档案合并</span></td>
        <td class="mono">…08001</td><td>相似度 0.94 ≥ 阈值 0.90，需人工确认主档</td>
        <td class="muted">1 小时 12 分</td><td>张敏</td>
        <td class="ops"><a href="#resolve">处理<span class="mark">②</span></a></td></tr>
      <tr><td class="mono">…50002</td><td><span class="tag tag-gray">高成本查询</span></td>
        <td class="mono">…08022</td><td>批量问答 42 份档案，预估成本 &gt; ¥2.00</td>
        <td class="muted">8 分钟</td><td>张敏</td>
        <td class="ops"><a href="#resolve">处理</a></td></tr>
    </tbody>
  </table>
</div>

<div class="grid" style="grid-template-columns:minmax(0,1fr) 420px;align-items:start">
  <div class="card" style="margin-bottom:0">
    <div class="card-hd"><h3>Agent 任务列表<span class="sub">TBL-25</span></h3>
      <select style="height:28px;font-size:12.5px"><option>全部状态</option><option>运行中</option>
        <option>待人工</option><option>成功</option><option>失败</option></select></div>
    <table>
      <thead><tr><th>任务 ID</th><th>图</th><th>触发</th><th>状态</th><th>进度</th><th>开始</th><th>操作</th></tr></thead>
      <tbody>
        <tr><td class="mono">…08001</td><td>parse_graph</td><td>手动</td>
          <td><span class="tag tag-warn">waiting_human</span></td>
          <td><div class="progress" style="width:88px"><i style="width:72%"></i></div></td>
          <td class="muted nowrap">14:05</td>
          <td class="ops"><a href="#replay">步骤回放</a></td></tr>
        <tr><td class="mono">…08022</td><td>ask_graph</td><td>手动</td>
          <td><span class="tag tag-warn">waiting_human</span></td>
          <td><div class="progress" style="width:88px"><i style="width:15%"></i></div></td>
          <td class="muted nowrap">16:41</td>
          <td class="ops"><a href="#replay">步骤回放</a></td></tr>
        <tr><td class="mono">…08040</td><td>parse_graph</td><td>定时</td>
          <td><span class="tag tag-info">running</span></td>
          <td><div class="progress" style="width:88px"><i style="width:41%"></i></div></td>
          <td class="muted nowrap">17:02</td>
          <td class="ops"><a href="#replay">步骤回放</a></td></tr>
        <tr><td class="mono">…07988</td><td>parse_graph</td><td>事件</td>
          <td><span class="tag tag-danger">failed</span></td>
          <td><div class="progress warn" style="width:88px"><i style="width:60%"></i></div></td>
          <td class="muted nowrap">13:20</td>
          <td class="ops"><a href="#replay">步骤回放</a><a href="#">重试</a></td></tr>
        <tr><td class="mono">…07901</td><td>parse_graph</td><td>手动</td>
          <td><span class="tag tag-ok">success</span></td>
          <td><div class="progress ok" style="width:88px"><i style="width:100%"></i></div></td>
          <td class="muted nowrap">11:30</td>
          <td class="ops"><a href="#replay">步骤回放</a></td></tr>
      </tbody>
    </table>
  </div>

  <div>
    <div class="card" id="replay">
      <div class="card-hd"><h3>步骤回放<span class="sub">TBL-26 · 只追加</span></h3>
        <span class="mono" style="font-size:12px">…08001</span></div>
      <div class="card-bd">
        <div class="timeline" style="padding-left:20px">
          <div class="tl-item"><div class="tl-head"><b>receive_files</b><span class="muted">0.3s</span></div>
            <div class="tl-body">输入 100 个文件；输出 100 个 file_id</div></div>
          <div class="tl-item"><div class="tl-head"><b>detect_format</b><span class="muted">1.4s</span></div>
            <div class="tl-body">96 文本类 · 3 扫描件 · 1 加密（失败分流）</div></div>
          <div class="tl-item"><div class="tl-head"><b>extract_fields</b><span class="muted">182s</span></div>
            <div class="tl-body">本地 qwen2.5:3b · 人均 18 字段 · 平均置信度 0.86</div></div>
          <div class="tl-item"><div class="tl-head"><b>normalize_dict</b><span class="muted">6s</span></div>
            <div class="tl-body">学历 → 字典码；标签只从受控词表选取</div></div>
          <div class="tl-item"><div class="tl-head"><b>persist_talent</b><span class="muted">14s</span></div>
            <div class="tl-body">写入 TBL-11~16 · 写变更留痕 TBL-22（change_source=0 AI 解析）</div></div>
          <div class="tl-item ai"><div class="tl-head"><b>build_vectors</b><span class="muted">28s</span></div>
            <div class="tl-body">脱敏后写 Milvus COL-01~03（明文身份<b>不入向量库</b>）</div></div>
          <div class="tl-item merge"><div class="tl-head"><b>detect_dup</b><span class="tag tag-warn">waiting_human</span></div>
            <div class="tl-body">检出 7 对重复候选，其中 1 对相似度 0.94 → 触发 <span class="mono">interrupt</span>，
            等待人工确认主档</div></div>
        </div>
      </div>
    </div>

    <div class="card" id="resolve">
      <div class="card-hd"><h3>处理待办<span class="sub">API-94</span></h3>
        <span class="tag tag-warn">interrupt …50001</span></div>
      <div class="card-bd">
        <table style="font-size:12.5px;margin-bottom:12px">
          <tbody>
            <tr><td>类型</td><td class="num mono">field_review / merge_confirm</td></tr>
            <tr><td>关联任务</td><td class="num mono">…08001</td></tr>
            <tr><td>恢复后下一节点</td><td class="num mono">persist_talent</td></tr>
          </tbody>
        </table>
        <div class="form-row" style="margin-bottom:11px">
          <label style="flex:0 0 66px">结论</label>
          <div class="ctl"><label class="radio"><input type="radio" name="ir" checked> 通过（approved）</label>
            <label class="radio"><input type="radio" name="ir"> 驳回（rejected）</label></div>
        </div>
        <div class="form-row" style="margin-bottom:11px">
          <label style="flex:0 0 66px">意见</label>
          <div class="ctl"><textarea rows="3" style="width:100%">确认以「李伟（数据治理部）」为主档，工作年限取 A 档 6 年。</textarea></div>
        </div>
        <div class="alert alert-info" style="margin:0 0 12px"><span class="i">◈</span>
          <div>处理后 LangGraph <b>从 interrupt 点恢复执行</b>（F-A0.1 状态持久化）：
          恢复后的图状态写 <span class="mono">agent_task.graph_state</span>，本步写 <span class="mono">agent_step</span>。<br>
          并发：他人已处理 → 40902；重复提交 → 幂等返回首次结果（40903）。</div></div>
        <button class="btn btn-primary">提交处理结论<span class="mark">③</span></button>
      </div>
    </div>
  </div>
</div>
"""
    st = states(
        sbox('loading — 任务表与回放骨架屏', s_loading(5)),
        sbox('empty — 无待办（理想态）',
             s_empty('当前没有人工介入待办', 'Agent 全部任务处于自动流转状态，无需人工确认')),
        sbox('conflict — 待办已被他人处理（code 40902）',
             '<div class="alert alert-warn" style="margin:0"><span class="i">!</span>'
             '<div><b>该待办已被 张敏 处理</b>（2026-09-21 16:41:02，结论：通过）<br>'
             '请刷新列表后继续处理其他待办。</div></div>'),
        sbox('failed — 重试耗尽进入人工队列（F-A0.4）',
             '<div class="alert alert-danger" style="margin:0"><span class="i">!</span>'
             '<div><b>任务 …07988 重试 3 次后仍失败，已进入人工处理队列并告警</b><br>'
             '失败节点：extract_fields（OCR 服务超时 50402）<br>'
             '可单文件重试（API-43）或整体重试（API-95）—— <b>不静默丢弃</b>。</div></div>'),
        sbox('forbidden — MGR / EMP 无 Agent 运维权限',
             s_forbidden('业务经理', '不可查看 Agent 任务与人工待办')),
    )
    leg = legend([
        '<b>对应：</b>F-A0.1~A0.5 · API-90~95 · TBL-25 / TBL-26 / TBL-27',
        '<b>状态：</b>默认 / 加载 / 空 / 并发冲突 / 重试耗尽 / 无权限',
        '<b>红线：</b>权威状态落 MySQL 非 Redis；三类日志只追加；interrupt 恢复必须从断点续跑',
        '<b>注意：</b>一期只需实现本页（interrupt + 待办 + 状态表）；'
        '需求 §10.4 的「Dify ⟷ LangGraph 回调契约」属 ⏸ F-2 阶段，两者不要混淆',
    ])
    return shell('p18-agent-task.html', 'P-18', 'Agent 任务与人工介入待办', '/ AI 中台 / Agent 任务',
                 'F-A0.1~A0.5', body + st + leg)


# ============================================================ P-19 数据字典与系统配置
def p19():
    body = """
<div class="alert alert-info"><span class="i">◈</span>
  <div><b>字典项与枚举的取值必须与数据库设计 §6.1 逐项一致</b> ——
  这是跨文档最常见的「同名不同值」不一致点：需求写「审核中」、DDL 写「待审核」、前端再写一套。
  <b>本项目统一为 <span class="mono">*_code</span>（int）+ <span class="mono">*_name</span> 同回。</b></div></div>

<div class="grid" style="grid-template-columns:268px minmax(0,1fr);align-items:start">
  <div class="card" style="margin-bottom:0">
    <div class="card-hd"><h3>字典类型<span class="sub">TBL-09</span></h3></div>
    <table style="font-size:13px">
      <thead><tr><th>类型编码</th><th>项数</th></tr></thead>
      <tbody>
        <tr style="background:#eaf1fd"><td class="mono" style="color:#1b5fd9;font-weight:600">education_level</td><td class="num">5</td></tr>
        <tr><td class="mono">gender</td><td class="num">3</td></tr>
        <tr><td class="mono">talent_data_status</td><td class="num">5</td></tr>
        <tr><td class="mono">parse_review_status</td><td class="num">4</td></tr>
        <tr><td class="mono">import_task_status</td><td class="num">5</td></tr>
        <tr><td class="mono">tag_dict_status</td><td class="num">3</td></tr>
        <tr><td class="mono">change_source</td><td class="num">4</td></tr>
        <tr><td class="mono">agent_task_status</td><td class="num">5</td></tr>
        <tr><td class="mono">ai_call_status</td><td class="num">2</td></tr>
      </tbody>
    </table>
    <div class="legend" style="padding:10px 13px;margin:0;border-top:1px solid #e3e8ef">
      <b>内建枚举</b>（写入 DDL 注释，与代码枚举一一对应）不可删项，只可改显示名<br>
      <b>业务字典</b>可增删改，但变更留痕
    </div>
  </div>

  <div>
    <div class="toolbar" style="margin-top:0">
      <input type="text" placeholder="搜索字典项">
      <select><option selected>全部状态</option><option>启用</option><option>停用</option></select>
      <button class="btn">查询</button>
      <span class="grow"></span>
      <button class="btn btn-primary">+ 新增字典项</button>
    </div>

    <div class="card">
      <div class="card-hd"><h3>学历 · education_level<span class="sub">5 个字典项</span></h3>
        <span class="muted" style="font-size:12px">业务字典（可维护）</span></div>
      <table>
        <thead><tr><th>项编码</th><th>显示名</th><th>排序</th><th>状态</th><th>引用档案数</th><th>操作</th></tr></thead>
        <tbody>
          <tr style="background:#fafcff"><td class="mono">01</td><td>高中及以下</td><td class="num">1</td>
            <td><span class="tag tag-ok">启用</span></td><td class="num">1,204</td>
            <td class="ops"><a href="#edit">编辑<span class="mark">①</span></a><a href="#">停用</a></td></tr>
          <tr><td class="mono">02</td><td>大专</td><td class="num">2</td>
            <td><span class="tag tag-ok">启用</span></td><td class="num">4,882</td>
            <td class="ops"><a href="#edit">编辑</a><a href="#">停用</a></td></tr>
          <tr><td class="mono">03</td><td>本科</td><td class="num">3</td>
            <td><span class="tag tag-ok">启用</span></td><td class="num">19,340</td>
            <td class="ops"><a href="#edit">编辑</a><a href="#">停用</a></td></tr>
          <tr><td class="mono">04</td><td>硕士</td><td class="num">4</td>
            <td><span class="tag tag-ok">启用</span></td><td class="num">11,206</td>
            <td class="ops"><a href="#edit">编辑</a><a href="#">停用</a></td></tr>
          <tr><td class="mono">05</td><td>博士</td><td class="num">5</td>
            <td><span class="tag tag-ok">启用</span></td><td class="num">1,572</td>
            <td class="ops"><a href="#edit">编辑</a><a href="#">停用</a></td></tr>
          <tr><td class="mono">06</td><td>中专</td><td class="num">0</td>
            <td><span class="tag tag-gray">停用</span></td><td class="num">0</td>
            <td class="ops"><a href="#">编辑</a><a href="#">启用</a></td></tr>
        </tbody>
      </table>
    </div>

    <div class="card" id="edit">
      <div class="card-hd"><h3>编辑字典项<span class="sub">API-26 · 改前值 / 改后值留痕</span></h3></div>
      <div class="card-bd">
        <div class="grid g2" style="gap:14px">
          <div class="form-row" style="margin:0">
            <label style="flex:0 0 88px"><span class="req">*</span>项编码</label>
            <div class="ctl"><input type="text" value="01" style="width:100%" readonly>
              <div class="form-help">编码已引用 1,204 条档案，<b>不允许修改</b>（改编码 = 破坏历史数据口径）</div></div>
          </div>
          <div class="form-row" style="margin:0">
            <label style="flex:0 0 88px"><span class="req">*</span>显示名</label>
            <div class="ctl"><input type="text" value="高中及以下" style="width:100%">
              <div class="form-help">仅影响展示，不影响已存数据</div></div>
          </div>
        </div>
        <div class="form-row" style="margin:14px 0 0">
          <label style="flex:0 0 88px">变更留痕</label>
          <div class="ctl">
            <table style="border:1px solid #e3e8ef;border-radius:5px;overflow:hidden">
              <thead><tr><th>时间</th><th>操作人</th><th>改前</th><th>改后</th></tr></thead>
              <tbody>
                <tr><td class="muted nowrap">09-14 10:31</td><td>张敏</td>
                  <td class="muted">高中/中专及以下</td><td>高中及以下</td></tr>
                <tr><td class="muted nowrap">03-02 10:11</td><td>系统</td>
                  <td class="muted">—</td><td>高中/中专及以下</td></tr>
              </tbody>
            </table>
          </div>
        </div>
        <div style="display:flex;gap:9px;margin-top:14px">
          <button class="btn btn-primary">保存<span class="mark">②</span></button>
          <button class="btn">取消</button>
        </div>
      </div>
    </div>
  </div>
</div>
"""
    st = states(
        sbox('loading — 类型表与字典项表骨架屏', s_loading(5)),
        sbox('empty — 新类型下暂无字典项',
             s_empty('该字典类型还没有字典项', '添加第一个字典项后，前端下拉框才能展示可选值',
                     '<button class="btn btn-primary">+ 新增字典项</button>')),
        sbox('conflict — 项编码重复（code 40901）',
             '<div class="alert alert-danger" style="margin:0"><span class="i">!</span>'
             '<div><b>项编码 03 在 education_level 下已存在</b>　请更换编码'
             '<code>（code 40901 / uk_dict_item(type_code, item_code)）</code></div></div>'),
        sbox('forbidden — 非 ADM 不可维护字典',
             s_forbidden('HR 管理员', '字典与系统配置仅 ADM 可维护（HR 可读字典项列表）')),
    )
    leg = legend([
        '<b>对应：</b>F-1.7 · API-23~27 · TBL-09 / TBL-10 / TBL-22',
        '<b>状态：</b>默认 / 加载 / 空 / 冲突 / 无权限',
        '<b>红线：</b>字典项与枚举取值必须与数据库设计 §6.1 逐项一致；变更必须留痕（改前值/改后值/操作人）',
        '<b>注意（评审重点）：</b>本页<b>未列入需求文档 §C.5 的一期 18 页清单</b>，'
        '但 F-1.7 是一期 P0 功能点且 API-23~27 需要页面承载 —— 建议补入清单，见评审重点确认项。',
    ])
    return shell('p19-system-config.html', 'P-19', '数据字典与系统配置', '/ 系统管理 / 数据字典',
                 'F-1.7', body + st + leg)


# ============================================================ index.html 评审入口
def index_html():
    cards = []
    for no, f, name, route, roles, keys, fp, apis, st in PAGES:
        cards.append(
            '<a class="pcard" href="%s">'
            '<div class="no">%s · %s</div>'
            '<div class="nm">%s</div>'
            '<div><span class="rt">%s</span></div>'
            '<div class="fp">角色：%s<br>功能点：%s<br>接口：%s</div>'
            '<div class="st-cov">状态：<i>%s</i></div>'
            '</a>' % (f, no, f, name, route, roles, fp, apis, st))
    grid = '<div class="pgrid">%s</div>' % ''.join(cards)

    rows = ''.join(
        '<tr><td class="mono nowrap">%s</td><td><a href="%s">%s</a></td>'
        '<td class="mono" style="font-size:12px">%s</td><td style="font-size:12.5px">%s</td>'
        '<td style="font-size:12px">%s</td><td class="mono" style="font-size:12px">%s</td>'
        '<td class="mono" style="font-size:12px">%s</td><td class="mono" style="font-size:12px">%s</td></tr>'
        % (no, f, name, route, roles, keys, fp, apis, f)
        for no, f, name, route, roles, keys, fp, apis, st in PAGES)
    detail = """
<div class="card">
  <div class="card-hd"><h3>页面明细<span class="sub">P-1 ~ P-19 · 可反查 F-x.y 与 API-nn</span></h3></div>
  <table>
    <thead><tr><th>编号</th><th>页面</th><th>路由</th><th>可见角色</th>
      <th>关键元素</th><th>功能点</th><th>接口</th><th>原型文件</th></tr></thead>
    <tbody>%s</tbody>
  </table>
</div>""" % rows

    state_tbl = """
<div class="card">
  <div class="card-hd"><h3>状态覆盖清单<span class="sub">每个列表页按需覆盖，避免开发期才发现空态/错误态</span></h3></div>
  <table>
    <thead><tr><th>状态</th><th>表现</th><th>已覆盖页面</th></tr></thead>
    <tbody>
      <tr><td>默认态</td><td>有数据的正常页面</td><td class="muted">P-1 ~ P-19 全部</td></tr>
      <tr><td>加载中</td><td>骨架屏 / spinner，禁用操作按钮</td><td class="muted">P-2 ~ P-19（除 P-1）</td></tr>
      <tr><td>空态</td><td>插画 + 引导按钮</td><td class="muted">P-3 ~ P-7、P-10、P-11、P-13、P-14、P-17 ~ P-19</td></tr>
      <tr><td>错误态</td><td>错误码 + trace_id + 重试</td><td class="muted">P-1 ~ P-7、P-9 ~ P-11、P-15 ~ P-19</td></tr>
      <tr><td>无权限（40301）</td><td>不显示操作按钮，不泄露资源存在性</td><td class="muted">P-2 ~ P-19</td></tr>
      <tr><td>边界数据</td><td>超长文本省略、空字段 <code>—</code>、大数字格式化</td><td class="muted">P-7（超长姓名 / 空年限 / 已删除）</td></tr>
      <tr><td>二次确认 / 并发冲突</td><td>合并、审批、待办、复核的冲突处理</td><td class="muted">P-5、P-8、P-10 ~ P-13、P-15、P-18、P-19</td></tr>
      <tr><td>降级态</td><td>HTTP 200 + degraded=true + 兜底文案</td><td class="muted">P-12（检索降级）、P-14（兜底 / 脱敏拦截）、P-16（熔断）</td></tr>
      <tr><td>脱敏态</td><td>字段级裁剪 / 占位符 / 🔒 标识</td><td class="muted">P-8、P-12、P-14、P-15、P-17</td></tr>
    </tbody>
  </table>
</div>"""

    confirm = """
<div class="card">
  <div class="card-hd"><h3>本次评审重点确认 8 条<span class="sub">请逐条给出结论</span></h3></div>
  <div class="card-bd">
    <table>
      <thead><tr><th style="width:40px">#</th><th>请重点确认</th><th style="width:180px">依据 / 影响</th></tr></thead>
      <tbody>
        <tr><td>1</td>
          <td><b>P-19「数据字典与系统配置」是否补入一期页面清单？</b><br>
            <span class="muted" style="font-size:12.5px">需求文档 §C.5 的「一期约 18 页」清单中<b>没有</b>这一页，
            但 F-1.7 是一期 P0 功能点，且 API-23~27 需要页面承载 —— 属清单遗漏，建议补入。</span></td>
          <td>F-1.7 · API-23~27<br><span class="tag tag-warn">清单不一致</span></td></tr>
        <tr><td>2</td>
          <td><b>导入单批上限（100 份）与单文件上限（50MB）是否需甲方书面确认？</b><br>
            <span class="muted" style="font-size:12.5px">Q-12。超限直接影响甲方实际使用体验，属验收口径；
            建议值配置化，避免改代码。</span></td>
          <td>Q-12 · F-3.1<br><span class="tag tag-danger">需确认</span></td></tr>
        <tr><td>3</td>
          <td><b>接口定义走「代码优先」还是「文档优先」？</b><br>
            <span class="muted" style="font-size:12.5px">Q-10。推荐代码优先：FastAPI 天然产出 OpenAPI，
            用 openapi-typescript 生成前端 TS 类型；接口设计文档作为契约规范与评审依据。</span></td>
          <td>Q-10 · 接口设计 §7</td></tr>
        <tr><td>4</td>
          <td><b>档案问答是否需要 WebSocket / 长连接？</b><br>
            <span class="muted" style="font-size:12.5px">Q-11。一期 SSE 单向流足够；若需「边生成边追问」
            或多端同步同一会话，需改协议。</span></td>
          <td>Q-11 · F-3.7</td></tr>
        <tr><td>5</td>
          <td><b>复核漏斗阈值（高影响面 0.85 / 低影响面 10% 抽样）是否按实际数据调整？</b><br>
            <span class="muted" style="font-size:12.5px">全量复核不可行（300 份 × 20 字段 × 30% ≈ 1800 次确认）。
            阈值本身可配，建议首轮导入后按实际复核量回调。</span></td>
          <td>D-8 · F-3.3</td></tr>
        <tr><td>6</td>
          <td><b>出网边界是否与甲方书面确认？</b><br>
            <span class="muted" style="font-size:12.5px">若甲方要求「100% 不出网（含脱敏片段）」，
            F-3.7 必须改用本地模型（qwen3:8b），接受生成质量降级 + 追加 GPU 采购 ——
            属<b>成本与质量重大变更</b>。</span></td>
          <td>Q-2 · §10.5<br><span class="tag tag-danger">高风险</span></td></tr>
        <tr><td>7</td>
          <td><b>技术偏离申报（12 项）是否已进投标技术偏离表？</b><br>
            <span class="muted" style="font-size:12.5px">F-2/F-4/F-5/F-6/F-7/F-8/F-10 对应原文「必须落地」条款，
            未如实申报可能被判实质性偏离<b>废标</b>。</span></td>
          <td>B-3 · §0.3.2<br><span class="tag tag-danger">废标风险</span></td></tr>
        <tr><td>8</td>
          <td><b>视觉风格是否沿用本原型的「浅色简洁政务风」？</b><br>
            <span class="muted" style="font-size:12.5px">原型为对齐信息架构与主流程，非视觉稿。
            正式前端视觉（含是否需要 AI 科技风）建议在 UI 设计阶段单独确认。</span></td>
          <td>原型规范 §一</td></tr>
      </tbody>
    </table>
  </div>
</div>"""

    notcov = """
<div class="card">
  <div class="card-hd"><h3>本版未覆盖项<span class="sub">明确边界，避免评审时被问住</span></h3></div>
  <div class="card-bd" style="font-size:13px;color:#5b6673;line-height:2">
    <b>① 后续阶段模块页面（⏸ 有意不做）</b> —— F-2 企业知识库问答、F-4 智能测评、F-5 NL2SQL 问数、
    F-6 岗位匹配、F-7 智能培训、F-8 Skills 编排、F-10 多端协同。<br>
    <b>② 移动端 / 响应式</b>（F-10.1，P2 规划项）；本原型按 1440px 桌面端设计。<br>
    <b>③ 站内信 / 邮件通知页面</b>（F-10.3）；仅在各页以文案形式提及「结果将通知您」。<br>
    <b>④ 真实交互</b> —— 页面之间为相对链接跳转（可点通），但表单提交、弹窗、抽屉为静态示意，
    不做真实接口调用。<br>
    <b>⑤ 图表为手绘内联 SVG 示意</b>（雷达图 / 成本柱状图），非 ECharts 真实数据；
    正式前端用 ECharts，数据源见接口设计 API-34 / API-89。<br>
    <b>⑥ 导出产物</b>（Excel / PDF / 个人数据包）仅标注入口，不含真实文件生成逻辑。
  </div>
</div>"""

    use = """
<div class="card">
  <div class="card-hd"><h3>使用与交付说明</h3></div>
  <div class="card-bd" style="font-size:13px;color:#5b6673;line-height:2">
    <b>零外部依赖</b>：全部 CSS 内联，不引任何 CDN，<b>断网可直接打开</b>；
    左侧导航与页面之间用相对链接互跳，可点通全站（需保持同目录）。<br>
    <b>设计系统统一</b>：全部 19 页共用同一套 <span class="mono">:root</span> 变量与通用 class
    （由 <span class="mono">_gen_prototype.py</span> 统一生成），避免各页各写一套配色。<br>
    <b>标注约定</b>：①②③ = 主流程点击顺序　🔒 = 需权限（旁注角色）　
    <span class="mono">&lt;!-- STATE: xxx --&gt;</span> = 状态分区（每页底部）<br>
    <b>与上游对齐</b>：字段名、按钮文案、枚举取值与《需求分析文档 v1.3》《数据库设计 v1.0》
    《接口设计 v1.0》<b>用词一致</b> —— 原型与接口对不上，联调时必然打架。<br>
    <b>假数据</b>：全部使用真实感数据（李伟 / 数据治理部 / 硕士 / 6 年 / 简历_李伟.pdf / IMP20260921001），
    不使用 xxx、test1 之类占位符 —— 否则看不出字段长度与排版问题。
  </div>
</div>"""

    body = """<div class="index-wrap">
  <div class="idx-hd">
    <h1>2026 顶配版 AI 数字化人才平台 · 一期原型</h1>
    <p>文件清单 <b>P-1 ~ P-19</b>（一期全站原型 + 评审入口）　·　设计文档套件 ⑦ 原型图　·
      对齐《需求分析文档 <b>v1.3</b>》《架构设计 <b>v1.0</b>》《数据库设计 <b>v1.0</b>》《接口设计 <b>v1.0</b>》</p>
    <div class="tags">
      <span class="tag tag-ok">浅色简洁政务风</span>
      <span class="tag tag-info">零外部依赖 · 离线可开</span>
      <span class="tag tag-info">1440px 桌面端</span>
      <span class="tag tag-warn">静态示意 · 非视觉稿</span>
      <span class="tag tag-line">一期范围：F-3 档案与数字画像 + 基座</span>
    </div>
  </div>

  <div class="grid g4" style="margin-bottom:22px">
    <div class="stat"><div class="lbl">原型页面</div><div class="num">19<small>页</small></div>
      <div class="sub">需求文档 §C.5 清单 18 页 + 补 1 页（P-19）</div></div>
    <div class="stat"><div class="lbl">一期功能点</div><div class="num">30<small>个</small></div>
      <div class="sub">F-3×13 + F-1×7 + F-9×3 + F-A0×7</div></div>
    <div class="stat"><div class="lbl">承载接口</div><div class="num">95<small>个</small></div>
      <div class="sub">API-01 ~ API-95 全覆盖</div></div>
    <div class="stat"><div class="lbl">状态覆盖</div><div class="num">9<small>类</small></div>
      <div class="sub">含降级 / 脱敏 / 并发冲突</div></div>
  </div>

  <h2 style="font-size:16px;margin:0 0 12px">页面导航<span class="muted"
    style="font-size:12.5px;font-weight:400;margin-left:8px">点击卡片直接打开对应原型</span></h2>
  %s

  %s
  %s
  %s
  %s

  <div class="legend" style="margin-top:20px">
    <b>文档地图（套件进度）：</b>① 需求分析 v1.3 ✅ → ② 架构设计 v1.0 ✅ → ③ 数据库设计 v1.0 ✅ →
    ④ 接口设计 v1.0 ✅ → ⑤⑥ 类图与时序图 → <b>⑦ 原型图 v1.0 ✅</b> → ⑧ 开发计划<br>
    <b>下一步（二选一）：</b>⑤⑥ 类图与时序图（领域模型 classDiagram + 分层类图 + SQ-4~SQ-6）／
    ⑧ 开发计划（WBS T-x.y + 里程碑 + 甘特图 + RACI）
  </div>
</div>""" % (grid, detail, state_tbl, confirm, notcov + use)

    css_extra = """
body{background:#f4f6f9}
.card{margin-bottom:16px}
h2{color:#1c2430}
"""
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>一期原型评审入口 · AI 数字化人才平台</title>
<style>%s%s</style>
</head>
<body>
%s
</body>
</html>""" % (CSS, css_extra, body)


# ============================================================ 主流程
BUILDERS = {
    'p01-login.html': p01,
    'p02-dashboard.html': p02,
    'p03-system-org.html': p03,
    'p04-system-user.html': p04,
    'p05-system-role.html': p05,
    'p06-system-audit.html': p06,
    'p07-talent-list.html': p07,
    'p08-talent-detail.html': p08,
    'p09-talent-import.html': p09,
    'p10-talent-review.html': p10,
    'p11-talent-dedup.html': p11,
    'p12-talent-search.html': p12,
    'p13-tag-dict.html': p13,
    'p14-talent-ask.html': p14,
    'p15-my-profile.html': p15,
    'p16-ai-model.html': p16,
    'p17-ai-calllog.html': p17,
    'p18-agent-task.html': p18,
    'p19-system-config.html': p19,
}


def main():
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    total = 0
    for fname in sorted(BUILDERS):
        html = BUILDERS[fname]()
        path = os.path.join(OUT, fname)
        with io.open(path, 'w', encoding='utf-8') as f:
            f.write(html)
        total += len(html)
        print('OK   %-26s %6.1f KB' % (fname, len(html) / 1024.0))
    idx = index_html()
    with io.open(os.path.join(OUT, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(idx)
    total += len(idx)
    print('OK   %-26s %6.1f KB' % ('index.html', len(idx) / 1024.0))
    print('---- %d 个文件 · 合计 %.1f KB' % (len(BUILDERS) + 1, total / 1024.0))


if __name__ == '__main__':
    main()


