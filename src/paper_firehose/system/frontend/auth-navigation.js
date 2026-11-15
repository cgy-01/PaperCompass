// Native links let the server check the current cookie, including new tabs and
// pages opened before a session expired. No provider IDs belong in the browser.
export function workspaceHref(target = "#home") {
  if (typeof target !== "string" || !target.startsWith("#") || target.length > 2048 || /[\r\n]/.test(target))
    target = "#home";
  return "/auth/github/login?return_to=" + encodeURIComponent(target);
}
export function authMessage(status) {
  return {
    cancelled: "你已取消 GitHub 授权，可以重新登录。",
    expired: "这次登录请求已过期，请重新发起登录。",
    failed: "暂时未能完成 GitHub 登录，请稍后重试。",
    unavailable: "GitHub 登录暂未开放，请稍后再试。",
  }[status] || "";
}
