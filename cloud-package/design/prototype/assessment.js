(function () {
  "use strict";

  var navItems = [
    { group: "", href: "p02-dashboard.html", icon: "◧", label: "工作台首页" },
    { group: "人才档案", href: "p07-talent-list.html", icon: "▤", label: "人才档案列表" },
    { group: "", href: "p12-talent-search.html", icon: "◎", label: "语义人才检索" },
    { group: "", href: "p14-talent-ask.html", icon: "✦", label: "档案 RAG 问答" },
    { group: "", href: "p09-talent-import.html", icon: "⇪", label: "批量导入" },
    { group: "", href: "p10-talent-review.html", icon: "✎", label: "字段复核工作台" },
    { group: "", href: "p11-talent-dedup.html", icon: "⧉", label: "重复档案合并" },
    { group: "", href: "p13-tag-dict.html", icon: "☰", label: "标签字典" },
    { group: "", href: "p15-my-profile.html", icon: "◔", label: "我的画像", lock: "🔒 本人" },
    { group: "人才测评", href: "p21-assessment-question.html", icon: "▦", label: "题库管理" },
    { group: "", href: "p22-assessment-paper.html", icon: "▤", label: "组卷工作台" },
    { group: "", href: "p23-assessment-task.html", icon: "◷", label: "测评任务" },
    { group: "", href: "p24-assessment-take.html", icon: "✓", label: "在线作答", lock: "🔒 EMP" },
    { group: "", href: "p25-assessment-monitor.html", icon: "◌", label: "结果与监控" },
    { group: "", href: "p26-assessment-report.html", icon: "▧", label: "能力报告" },
    { group: "AI 中台", href: "p16-ai-model.html", icon: "⚙", label: "模型与 Prompt", lock: "🔒 AIOPS" },
    { group: "", href: "p17-ai-calllog.html", icon: "▦", label: "调用日志与成本", lock: "🔒 ADM/AIOPS" },
    { group: "", href: "p18-agent-task.html", icon: "⚑", label: "Agent 任务与待办" },
    { group: "系统管理", href: "p03-system-org.html", icon: "⌂", label: "组织架构", lock: "🔒 ADM/HR" },
    { group: "", href: "p04-system-user.html", icon: "☺", label: "用户管理", lock: "🔒 ADM" },
    { group: "", href: "p05-system-role.html", icon: "⛨", label: "角色与权限", lock: "🔒 ADM" },
    { group: "", href: "p19-system-config.html", icon: "≡", label: "数据字典与配置", lock: "🔒 ADM" },
    { group: "", href: "p06-system-audit.html", icon: "◫", label: "审计日志", lock: "🔒 ADM/AUDIT" }
  ];

  function assessmentNavigation() {
    var current = window.location.pathname.split("/").pop() || "p02-dashboard.html";
    var links = [];
    navItems.forEach(function (item) {
      if (item.group) links.push('<div class="grp">' + item.group + '</div>');
      var active = item.href === current;
      links.push('<a href="' + item.href + '"' + (active ? ' class="active" aria-current="page"' : '') + '>' +
        '<span class="ico" aria-hidden="true">' + item.icon + '</span>' +
        '<span class="lbl">' + item.label + '</span>' +
        (item.lock ? '<span class="lock">' + item.lock + '</span>' : '') +
        '</a>');
    });
    return '<div class="brand"><span class="dot">才</span><span class="brand-copy"><b>AI 人才平台</b><small>一期原型</small></span>' +
      '<button class="sidebar-toggle" type="button" aria-label="收起导航" title="收起导航">‹</button></div>' +
      '<nav class="nav" aria-label="主导航">' + links.join("") + '</nav>' +
      '<div class="userbox"><span class="avatar">张</span><span class="user-meta"><b>张敏</b><small>HR 管理员 · 人力资源部</small></span></div>';
  }

  function bindAssessmentNavigation() {
    var sidebar = document.querySelector(".sidebar");
    if (!sidebar) return;
    sidebar.innerHTML = assessmentNavigation();
    var toggle = sidebar.querySelector("[data-sidebar-toggle]") || sidebar.querySelector(".sidebar-toggle");
    if (toggle) toggle.addEventListener("click", function () { document.body.classList.toggle("sidebar-collapsed"); });
  }

  function showToast(message) {
    var node = document.querySelector("[data-toast]");
    if (!node) {
      node = document.createElement("div");
      node.className = "toast";
      node.setAttribute("data-toast", "");
      document.body.appendChild(node);
    }
    node.textContent = message;
    node.classList.add("show");
    window.clearTimeout(node._timer);
    node._timer = window.setTimeout(function () { node.classList.remove("show"); }, 2400);
  }

  function toggleNav() {
    document.body.classList.toggle("nav-open");
  }

  function switchTab(button, group) {
    var scope = button.closest("[data-tab-scope]") || document;
    scope.querySelectorAll("[data-tab-group='" + group + "']").forEach(function (item) {
      item.classList.toggle("active", item === button);
    });
    scope.querySelectorAll("[data-panel-group='" + group + "']").forEach(function (panel) {
      panel.classList.toggle("active", panel.dataset.panel === button.dataset.tab);
    });
  }

  function setActiveMode(button, group) {
    var scope = button.closest("[data-mode-scope]") || document;
    scope.querySelectorAll("[data-mode-group='" + group + "']").forEach(function (item) {
      item.classList.toggle("active", item === button);
    });
    scope.querySelectorAll("[data-mode-panel-group='" + group + "']").forEach(function (panel) {
      panel.hidden = panel.dataset.modePanel !== button.dataset.mode;
    });
  }

  function openPanel(selector) {
    var panel = document.querySelector(selector);
    if (panel) panel.classList.add("open");
  }

  function closePanel(selector) {
    var panel = document.querySelector(selector);
    if (panel) panel.classList.remove("open");
  }

  function bindCommon() {
    bindAssessmentNavigation();
    document.querySelectorAll("[data-nav-toggle]").forEach(function (button) {
      button.addEventListener("click", toggleNav);
    });
    document.querySelectorAll("[data-tab]").forEach(function (button) {
      button.addEventListener("click", function () { switchTab(button, button.dataset.tabGroup); });
    });
    document.querySelectorAll("[data-mode]").forEach(function (button) {
      button.addEventListener("click", function () { setActiveMode(button, button.dataset.modeGroup); });
    });
    document.querySelectorAll("[data-open]").forEach(function (button) {
      button.addEventListener("click", function () { openPanel(button.dataset.open); });
    });
    document.querySelectorAll("[data-close]").forEach(function (button) {
      button.addEventListener("click", function () { closePanel(button.dataset.close); });
    });
    document.querySelectorAll("[data-toast-message]").forEach(function (button) {
      button.addEventListener("click", function () { showToast(button.dataset.toastMessage); });
    });
  }

  window.AssessmentProto = {
    showToast: showToast,
    toggleNav: toggleNav,
    switchTab: switchTab,
    setActiveMode: setActiveMode,
    openPanel: openPanel,
    closePanel: closePanel,
  };

  document.addEventListener("DOMContentLoaded", bindCommon);
}());
