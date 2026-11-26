"use strict";
const $ = (id) => document.getElementById(id);
let key = sessionStorage.getItem("admin") || "",
  model,
  selected,
  binding,
  dirty = false;
function note(text = "", error = false) {
  $("msg").textContent = text;
  $("msg").className = error ? "error" : "";
}
async function api(path, body) {
  const r = await fetch("/api/" + path, {
    method: body ? "POST" : "GET",
    headers: {
      Authorization: "Bearer " + key,
      "Content-Type": "application/json",
    },
    body: body && JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok) {
    const e = new Error(data.error || "连接失败");
    e.status = r.status;
    throw e;
  }
  return data;
}
function elt(tag, text, cls) {
  const n = document.createElement(tag);
  if (text != null) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
function showLogin() {
  key = "";
  sessionStorage.removeItem("admin");
  $("panel").hidden = true;
  $("logout").hidden = true;
  $("login").hidden = false;
  $("key").value = "";
}
function report(e) {
  if (e.status === 401) showLogin();
  note(e.message, true);
}
async function load(preferred) {
  model = await api("manage");
  sessionStorage.setItem("admin", key);
  $("login").hidden = true;
  $("panel").hidden = false;
  $("logout").hidden = false;
  selected = model.users.find((u) => u.id === preferred) || model.users[0];
  $("summary").textContent =
    model.users.length +
    " 位收件人 · " +
    model.traders.length +
    " 位监控交易员";
  render();
  note(model.stale ? "交易员目录暂未同步，正在显示部署时的名单。" : "");
}
function render() {
  const list = $("users");
  list.replaceChildren();
  dirty = false;
  for (const u of model.users) {
    const b = elt("button", null, "person");
    b.type = "button";
    b.setAttribute("aria-pressed", String(selected?.id === u.id));
    const v = elt("span");
    v.append(elt("b", u.label), elt("small", u.enabled ? "接收中" : "已暂停"));
    b.append(elt("span", Array.from(u.label)[0], "avatar"), v);
    b.onclick = () => {
      if (canLeave()) {
        selected = u;
        render();
      }
    };
    list.append(b);
  }
  $("empty").hidden = !!selected;
  $("prefs").hidden = !selected;
  if (!selected) return;
  $("username").textContent = selected.label;
  $("label").value = selected.label;
  $("enabled").checked = selected.enabled;
  $("all").checked = selected.traders === null;
  const tr = $("traders");
  tr.replaceChildren();
  for (const t of model.traders) {
    const label = elt("label", null, "trader"),
      box = elt("input");
    box.type = "checkbox";
    box.value = t.portfolio_id;
    box.checked =
      selected.traders === null || selected.traders.includes(t.portfolio_id);
    label.append(box, document.createTextNode(t.nickname));
    tr.append(label);
  }
  update();
}
function values() {
  return {
    label: $("label").value.trim(),
    enabled: $("enabled").checked,
    traders: $("all").checked
      ? null
      : [...$("traders").querySelectorAll("input:checked")].map((x) => x.value),
  };
}
function update() {
  if (!selected) return;
  const v = values();
  dirty =
    v.label !== selected.label ||
    v.enabled !== selected.enabled ||
    JSON.stringify(v.traders?.slice().sort()) !==
      JSON.stringify(selected.traders?.slice().sort());
  for (const b of $("traders").querySelectorAll("input"))
    b.disabled = $("all").checked;
  $("count").textContent =
    "已选 " +
    (v.traders?.length ?? model.traders.length) +
    " / " +
    model.traders.length;
  $("save").disabled = $("reset").disabled = !dirty;
  $("change").textContent = dirty ? "有未保存的修改" : "设置已保存";
}
function canLeave() {
  return !dirty || confirm("放弃尚未保存的修改？");
}
window.addEventListener("beforeunload", (e) => {
  if (dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});
$("loginForm").onsubmit = async (e) => {
  e.preventDefault();
  key = $("key").value.trim();
  try {
    await load();
    $("key").value = "";
  } catch (err) {
    report(err);
  }
};
$("logout").onclick = () => {
  if (canLeave()) {
    dirty = false;
    showLogin();
    note();
  }
};
$("prefs").oninput = update;
$("all").onchange = () => {
  if ($("all").checked)
    for (const b of $("traders").querySelectorAll("input")) b.checked = true;
  update();
};
$("reset").onclick = render;
$("prefs").onsubmit = async (e) => {
  e.preventDefault();
  const u = selected,
    v = values();
  $("save").disabled = true;
  try {
    const r = await api("save", { ...v, id: u.id, revision: u.revision });
    Object.assign(u, v, { revision: r.revision });
    render();
    note("已保存订阅设置，下轮监控生效。");
  } catch (err) {
    report(err);
    update();
  }
};
const dlg = $("bind");
function clearBind() {
  binding = null;
  $("qr").replaceChildren();
  $("progress").hidden = true;
  $("newBind").hidden = false;
  $("retry").hidden = true;
  $("pinForm").hidden = true;
  $("phrase").textContent = "";
}
$("add").onclick = () => {
  if (!canLeave()) return;
  if (dirty) render();
  clearBind();
  $("newlabel").value = "";
  dlg.showModal();
};
$("close").onclick = () => dlg.close();
dlg.onclose = () => {
  const id = binding?.id;
  clearBind();
  if (id) api("cancel", { id }).catch(() => {});
};
$("newBind").onsubmit = async (e) => {
  e.preventDefault();
  const button = e.submitter || $("newBind").querySelector("button");
  button.disabled = true;
  try {
    const result = await api("bind", { label: $("newlabel").value });
    if (!dlg.open) {
      api("cancel", { id: result.id }).catch(() => {});
      return;
    }
    binding = result;
    const qr = qrcode(0, "M");
    qr.addData(result.qr);
    qr.make();
    const img = elt("img");
    img.src = qr.createDataURL(5, 18);
    img.alt = "用要接收提醒的微信扫码";
    $("qr").replaceChildren(img);
    $("progress").hidden = false;
    $("newBind").hidden = true;
    $("stage").textContent = "请用要接收提醒的微信扫码，并在手机上确认。";
    poll(result);
  } catch (err) {
    $("progress").hidden = false;
    $("stage").textContent = err.message;
  } finally {
    button.disabled = false;
  }
};
async function poll(b, pin) {
  if (!b || b.busy) return;
  b.busy = true;
  $("retry").hidden = true;
  try {
    while (binding === b && dlg.open) {
      const r = await api("poll", { id: b.id, pin });
      pin = undefined;
      if (binding !== b) return;
      if (r.phase === "done") {
        binding = null;
        dlg.close();
        await load(r.user);
        note("微信已绑定。请确认接收开关和交易员选择。");
        return;
      }
      if (r.phase === "pin") {
        $("pinForm").hidden = false;
        $("stage").textContent = "请输入手机微信显示的 PIN。";
        return;
      }
      if (r.phase === "message") {
        $("qr").replaceChildren();
        $("stage").textContent = "请在刚绑定的机器人私聊中发送以下短语：";
        $("phrase").textContent = r.challenge;
      }
      if (r.phase === "scanned")
        $("stage").textContent = "已扫码，请在微信确认。";
      await new Promise((resolve) => setTimeout(resolve, 1300));
    }
  } catch (err) {
    if (binding !== b) return;
    $("stage").textContent = err.message;
    $("retry").hidden = false;
    b.expired = err.status === 410;
    $("retry").textContent = b.expired ? "重新生成二维码" : "重试";
  } finally {
    b.busy = false;
  }
}
$("pinForm").onsubmit = (e) => {
  e.preventDefault();
  const pin = $("pin").value;
  $("pin").value = "";
  $("pinForm").hidden = true;
  poll(binding, pin);
};
$("retry").onclick = () => {
  if (binding?.expired) {
    clearBind();
    $("newBind").requestSubmit();
  } else poll(binding);
};
if (key) load().catch(report);
