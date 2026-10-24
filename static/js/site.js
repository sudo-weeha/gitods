// GitODS 站点脚本：下拉菜单行为、分支过滤与复制按钮（渐进增强，页面无 JS 也可用）。
(function () {
  "use strict";

  var menus = function () {
    return document.querySelectorAll("details.menu[open]");
  };

  function closeMenus(except) {
    menus().forEach(function (details) {
      if (details !== except) {
        details.removeAttribute("open");
      }
    });
  }

  document.addEventListener("click", function (event) {
    var menu = event.target.closest ? event.target.closest("details.menu") : null;
    if (menu) {
      // 点击其他 summary 时先收起兄弟菜单
      if (event.target.closest("summary")) {
        closeMenus(menu);
      }
      return; // 面板内部点击交给默认行为
    }
    closeMenus();
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      closeMenus();
    }
  });

  // 文件树：当前条目滚进可视区（页面加载时）
  var currentTreeItem = document.querySelector(
    ".tree-file.is-current .tree-link, .tree-dir.is-current > details > summary .tree-link"
  );
  if (currentTreeItem && currentTreeItem.scrollIntoView) {
    currentTreeItem.scrollIntoView({ block: "nearest" });
  }

  // 分支下拉的名称过滤
  document.addEventListener("input", function (event) {
    var input = event.target.closest ? event.target.closest("[data-filter-list]") : null;
    if (!input) {
      return;
    }
    var list = input.parentElement.querySelector(".menu-list");
    if (!list) {
      return;
    }
    var keyword = input.value.trim().toLowerCase();
    list.querySelectorAll("li").forEach(function (li) {
      li.hidden = li.textContent.toLowerCase().indexOf(keyword) === -1;
    });
  });

  // 复制按钮：data-copy-text 直接复制；data-copy-url 拉取 raw 文本后复制
  function flash(button) {
    button.classList.add("copied");
    setTimeout(function () {
      button.classList.remove("copied");
    }, 1500);
  }

  function legacyCopy(text) {
    var helper = document.createElement("textarea");
    helper.value = text;
    helper.style.position = "fixed";
    helper.style.opacity = "0";
    document.body.appendChild(helper);
    helper.select();
    try {
      document.execCommand("copy");
    } catch (error) {
      /* 忽略：剪贴板不可用时仅不闪提示 */
    }
    helper.remove();
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest
      ? event.target.closest("[data-copy-text],[data-copy-url]")
      : null;
    if (!button) {
      return;
    }
    event.preventDefault();
    if (button.dataset.copyText !== undefined) {
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(button.dataset.copyText).then(function () {
          flash(button);
        });
      } else {
        legacyCopy(button.dataset.copyText);
        flash(button);
      }
      return;
    }
    fetch(button.dataset.copyUrl)
      .then(function (response) {
        return response.text();
      })
      .then(function (text) {
        if (navigator.clipboard && window.isSecureContext) {
          return navigator.clipboard.writeText(text);
        }
        legacyCopy(text);
        return null;
      })
      .then(function () {
        flash(button);
      })
      .catch(function () {
        /* 复制失败保持安静，用户可手动选择 */
      });
  });
})();
