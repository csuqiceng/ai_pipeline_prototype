const input = document.querySelector("#chatInput");
const sendBtn = document.querySelector("#sendBtn");
const chat = document.querySelector(".chat-panel");
const sceneTitle = document.querySelector("#sceneTitle");

const sceneReplies = {
  idle: "智能协同对话",
  precheck: "安全预检：限位、报警、通道忙闲、速度参数都会在执行前检查。",
  execute: "执行监控：这里会显示当前步骤、估算进度、控制器回显和完成状态。",
  alarm: "报警处理：发生报警时会主动播报风险，并提供复位、确认报警等操作。",
  settings: "参数设置：安全参数变更需要二次确认，不允许 AI 直接修改。"
};

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function appendMessage(role, text) {
  const row = document.createElement("div");
  row.className = `message-row ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "我" : "AI";

  const bubble = document.createElement("article");
  bubble.className = "bubble";
  bubble.innerHTML = `<p>${escapeHtml(text).replaceAll("\n", "<br>")}</p>`;

  if (role === "user") {
    row.append(bubble, avatar);
  } else {
    row.append(avatar, bubble);
  }

  chat.append(row);
  chat.scrollTop = chat.scrollHeight;
}

function sendMessage() {
  const text = input.value.trim();
  if (!text) return;
  appendMessage("user", text);
  input.value = "";

  window.setTimeout(() => {
    appendMessage(
      "assistant",
      "这是完整界面 demo 的模拟回复。正式接入后，我会读取七类看板、调用 DeepSeek 生成自然语言分析，并在本地白名单校验通过后生成待确认计划。"
    );
  }, 180);
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    const scene = button.dataset.scene;
    sceneTitle.textContent = sceneReplies[scene] || button.textContent;
    appendMessage("assistant", `已切换到「${button.textContent}」场景。${sceneReplies[scene] || ""}`);
  });
});

sendBtn.addEventListener("click", sendMessage);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
});
