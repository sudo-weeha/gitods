// 上传真实进度：显示已上传字节百分比；服务端处理阶段只显示状态文案，
// 不伪造服务端处理百分比。无 JS 时表单按普通提交工作。
(function () {
  "use strict";

  var form = document.getElementById("upload-form");
  if (!form) {
    return;
  }
  var bar = document.getElementById("upload-progress");
  var fill = bar ? bar.querySelector(".progress-fill") : null;
  var text = bar ? bar.querySelector(".progress-text") : null;

  form.addEventListener("submit", function (event) {
    if (!window.XMLHttpRequest || !window.FormData) {
      return; // 老浏览器退化为普通提交
    }
    event.preventDefault();
    var data = new FormData(form);
    var xhr = new XMLHttpRequest();
    xhr.open(form.method, form.getAttribute("action"));
    // 保持默认 responseType（text）：document 模式下无法读 responseText

    if (bar) {
      bar.style.display = "block";
    }
    if (text) {
      text.textContent = "准备上传…";
    }
    xhr.upload.onprogress = function (progress) {
      if (!progress.lengthComputable) {
        return;
      }
      var percent = Math.round((progress.loaded / progress.total) * 100);
      if (fill) {
        fill.style.width = percent + "%";
      }
      if (text) {
        text.textContent = "上传中 " + percent + "%";
      }
    };
    xhr.upload.onload = function () {
      if (text) {
        text.textContent = "服务器处理中…"; // 处理阶段不伪造百分比
      }
    };
    xhr.onerror = function () {
      if (text) {
        text.textContent = "上传失败，请检查网络后重试。";
      }
    };
    xhr.onload = function () {
      var finalUrl = "";
      try {
        finalUrl = xhr.responseURL || "";
      } catch (error) {
        finalUrl = "";
      }
      var path = "";
      try {
        path = new URL(finalUrl, location.href).pathname;
      } catch (error) {
        path = "";
      }
      var backToManage =
        path.indexOf("/admin/catalog/project/") === 0 && path.indexOf("/files/") !== -1;
      if (finalUrl && !backToManage) {
        // 成功（或跳往公开页）：直接导航
        location.href = finalUrl;
        return;
      }
      // 管理页重渲染（错误/冲突）：用响应文档替换当前页
      document.open();
      document.write(xhr.responseText);
      document.close();
    };
    xhr.send(data);
  });
})();
