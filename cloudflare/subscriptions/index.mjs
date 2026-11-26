import { DurableObject } from "cloudflare:workers";
import initialTraders from "../../traders.json";

const ILink = "https://ilinkai.weixin.qq.com";
const TTL = 10 * 60 * 1000;
const json = (data, status = 200) =>
  Response.json(data, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
class Problem extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}
function origin(value) {
  const url = new URL(value);
  if (
    url.protocol !== "https:" ||
    !url.hostname.endsWith(".weixin.qq.com") ||
    url.username ||
    url.password ||
    url.port ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  )
    throw new Problem(502, "微信返回的接口地址无效");
  return url.origin;
}
async function ilink(base, path, body, token) {
  const headers = {
    "iLink-App-Id": "bot",
    "iLink-App-ClientVersion": "196608",
  };
  if (body) {
    headers["Content-Type"] = "application/json";
    headers.AuthorizationType = "ilink_bot_token";
    headers["X-WECHAT-UIN"] = btoa(
      String(crypto.getRandomValues(new Uint32Array(1))[0]),
    );
    if (token) {
      headers.Authorization = "Bearer " + token;
      body = {
        ...body,
        base_info: {
          channel_version: "3.0.0",
          bot_agent: "YingCapitalMonitor/4.0",
        },
      };
    }
  }
  const response = await fetch(origin(base) + "/ilink/bot/" + path, {
    method: body ? "POST" : "GET",
    headers,
    body: body && JSON.stringify(body),
    redirect: "manual",
    signal: AbortSignal.timeout(40000),
  });
  if (!response.ok) throw new Problem(502, "微信接口暂时不可用，请重试");
  const result = await response.json();
  if ((result.ret ?? 0) !== 0 || (result.errcode ?? 0) !== 0)
    throw new Problem(502, "微信未接受请求，请检查绑定或重试");
  return result;
}
function secretMatches(request, value) {
  return (
    Boolean(value) && request.headers.get("Authorization") === "Bearer " + value
  );
}
function labelOf(value) {
  if (typeof value !== "string" || !value.trim() || value.trim().length > 40)
    throw new Problem(400, "请输入 1 至 40 字的收件人备注");
  return value.trim();
}
async function traderList() {
  const response = await fetch(
    "https://api.github.com/repos/fenggegang/yingcapital-position-monitor/contents/traders.json?ref=main",
    {
      headers: {
        Accept: "application/vnd.github.raw+json",
        "User-Agent": "yingcapital-subscriptions",
      },
      cf: { cacheEverything: true, cacheTtl: 60 },
      signal: AbortSignal.timeout(7000),
    },
  );
  if (!response.ok) throw new Error("目录暂不可用");
  return (await response.json()).filter((t) => t.enabled);
}
export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (!path.startsWith("/api/")) return env.ASSETS.fetch(request);
    const machine = path === "/api/runtime";
    if (!secretMatches(request, machine ? env.RUNTIME_KEY : env.ADMIN_KEY))
      return json({ error: "管理密钥无效或登录已失效" }, 401);
    try {
      return await env.STORE.getByName("subscriptions").fetch(request);
    } catch {
      return json({ error: "配置服务暂时不可用" }, 503);
    }
  },
};
export class Subscriptions extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.db = ctx.storage;
    this.polls = new Set();
  }
  async alarm() {
    const rows = await this.db.list({ prefix: "bind:" });
    const future = [];
    for (const [key, bind] of rows) {
      if (bind.expires <= Date.now()) await this.db.delete(key);
      else future.push(bind.expires);
    }
    if (future.length) await this.db.setAlarm(Math.min(...future));
  }
  async fetch(req) {
    const path = new URL(req.url).pathname;
    try {
      if (
        req.method === "GET" &&
        ["/api/runtime", "/api/manage"].includes(path)
      ) {
        const users = [...(await this.db.list({ prefix: "user:" })).values()];
        if (path === "/api/runtime")
          return json({
            users: users.map(({ id, enabled, traders, session }) => ({
              id,
              enabled,
              traders,
              session,
            })),
          });
        let traders,
          stale = false;
        try {
          traders = await traderList();
        } catch {
          traders = initialTraders.filter((t) => t.enabled);
          stale = true;
        }
        return json({
          traders,
          stale,
          users: users.map(({ session, ...visible }) => visible),
        });
      }
      if (req.method !== "POST") throw new Problem(405, "请求方式无效");
      const data = await req.json();
      if (path === "/api/import") {
        const s = data.session;
        if (
          !s ||
          ![s.bot_token, s.context_token, s.user_id].every(
            (v) => typeof v === "string" && v.length,
          )
        )
          throw new Problem(400, "缺少原账号会话");
        const session = {
          base_url: origin(s.base_url),
          bot_token: s.bot_token,
          user_id: s.user_id,
          context_token: s.context_token,
        };
        await this.db.transaction(async (tx) => {
          if (await tx.get("user:weixin")) return;
          await tx.put("user:weixin", {
            id: "weixin",
            label: "我的微信",
            enabled: true,
            traders: null,
            revision: 1,
            session,
          });
        });
        return json({ ok: true });
      }
      if (path === "/api/save") {
        const label = labelOf(data.label);
        if (
          typeof data.enabled !== "boolean" ||
          !(
            data.traders === null ||
            (Array.isArray(data.traders) &&
              data.traders.every(
                (t) => typeof t === "string" && /^\d+$/.test(t),
              ))
          )
        )
          throw new Problem(400, "订阅配置无效");
        const user = await this.db.transaction(async (tx) => {
          const key = "user:" + data.id,
            u = await tx.get(key);
          if (!u) throw new Problem(404, "收件人不存在");
          if (u.revision !== data.revision)
            throw new Problem(409, "配置已被更新，请重新载入");
          Object.assign(u, {
            label,
            enabled: data.enabled,
            traders: data.traders && [...new Set(data.traders)],
            revision: u.revision + 1,
          });
          await tx.put(key, u);
          return u;
        });
        return json({ ok: true, revision: user.revision });
      }
      if (path === "/api/bind") {
        const label = labelOf(data.label),
          id = crypto.randomUUID();
        const qr = await ilink(ILink, "get_bot_qrcode?bot_type=3", {
          local_token_list: [],
        });
        if (!qr.qrcode || typeof qr.qrcode_img_content !== "string")
          throw new Problem(502, "未取得微信二维码");
        const expires = Date.now() + TTL;
        await this.db.put("bind:" + id, {
          label,
          expires,
          base: ILink,
          qr: qr.qrcode,
          phase: "wait",
          challenge: "绑定 " + crypto.randomUUID().slice(0, 8),
        });
        if (!(await this.db.getAlarm())) await this.db.setAlarm(expires);
        return json({ id, qr: qr.qrcode_img_content, expires });
      }
      if (path === "/api/cancel") {
        await this.db.delete("bind:" + data.id);
        return json({ ok: true });
      }
      if (path === "/api/poll") {
        if (this.polls.has(data.id)) throw new Problem(409, "正在检查绑定状态");
        this.polls.add(data.id);
        try {
          return json(await this.poll(data));
        } finally {
          this.polls.delete(data.id);
        }
      }
      throw new Problem(404, "接口不存在");
    } catch (e) {
      if (e.name === "TimeoutError" && path === "/api/poll")
        return json({ phase: "again" });
      return json(
        { error: e instanceof Problem ? e.message : "请求未完成，请重试" },
        e.status || 502,
      );
    }
  }
  async poll({ id, pin }) {
    const key = "bind:" + id,
      b = await this.db.get(key);
    if (!b || b.expires <= Date.now())
      throw new Problem(410, "绑定已过期，请重新生成二维码");
    if (b.phase === "done") return { phase: "done", user: b.user };
    if (b.phase === "message") {
      const s = b.session;
      const r = await ilink(
        s.base_url,
        "getupdates",
        { get_updates_buf: b.cursor || "" },
        s.bot_token,
      );
      b.cursor = r.get_updates_buf || b.cursor || "";
      const hit = (r.msgs || []).find(
        (m) =>
          m.from_user_id === s.user_id &&
          m.context_token &&
          m.item_list?.some((i) => i.text_item?.text?.trim() === b.challenge),
      );
      if (hit) {
        s.context_token = hit.context_token;
        return this.db.transaction(async (tx) => {
          if (!(await tx.get(key)) || b.expires <= Date.now())
            throw new Problem(410, "绑定已结束");
          const old = [...(await tx.list({ prefix: "user:" })).values()].find(
            (u) => u.session.user_id === s.user_id,
          );
          const user = old || {
            id: crypto.randomUUID(),
            label: b.label,
            enabled: false,
            traders: [],
            revision: 0,
          };
          user.session = s;
          user.revision++;
          await tx.put("user:" + user.id, user);
          await tx.put(key, {
            phase: "done",
            user: user.id,
            expires: b.expires,
          });
          return { phase: "done", user: user.id };
        });
      }
    } else {
      const query = new URLSearchParams({ qrcode: b.qr });
      if (pin) query.set("verify_code", String(pin));
      const r = await ilink(b.base, "get_qrcode_status?" + query);
      if (r.status === "confirmed" && r.bot_token && r.ilink_user_id) {
        b.session = {
          base_url: origin(r.baseurl || b.base),
          bot_token: r.bot_token,
          user_id: r.ilink_user_id,
        };
        b.phase = "message";
      } else if (r.status === "scaned_but_redirect") {
        b.base = origin("https://" + r.redirect_host);
        b.phase = "scanned";
      } else if (
        ["need_verifycode", "confirmed_but_need_verifycode"].includes(r.status)
      )
        b.phase = "pin";
      else if (["scaned", "wait"].includes(r.status))
        b.phase = r.status === "scaned" ? "scanned" : "wait";
      else throw new Problem(410, "二维码已失效，请重新生成");
    }
    await this.db.transaction(async (tx) => {
      if (!(await tx.get(key)) || b.expires <= Date.now())
        throw new Problem(410, "绑定已结束");
      await tx.put(key, b);
    });
    return {
      phase: b.phase,
      challenge: b.phase === "message" ? b.challenge : undefined,
    };
  }
}
