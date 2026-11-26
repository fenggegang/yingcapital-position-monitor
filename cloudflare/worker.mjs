const dispatchUrl = "https://api.github.com/repos/fenggegang/yingcapital-position-monitor/actions/workflows/monitor.yml/dispatches";

export default {
  async scheduled(_event, env) {
    if (!env.GITHUB_DISPATCH_TOKEN) {
      console.warn("Waiting for GITHUB_DISPATCH_TOKEN; skipping dispatch");
      return;
    }
    const response = await fetch(dispatchUrl, {
      method: "POST",
      redirect: "manual",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
        "Content-Type": "application/json",
        "User-Agent": "yingcapital-monitor-trigger",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({ ref: "main" }),
    });
    if (!response.ok) {
      throw new Error(`GitHub workflow dispatch returned HTTP ${response.status}`);
    }
  },
};
