/**
 * 文件职责：为 Plugin Page 提供不会因 sandbox opaque origin 崩溃的本地存储适配。
 *
 * AstrBot Plugin Page iframe 默认没有 allow-same-origin，浏览器可能拒绝
 * 访问 localStorage。管理端的缓存属于增强功能，不能因为它不可用而阻断
 * React 首屏渲染，因此这里在异常时降级到当前页面内存缓存。
 */

(function () {
    const memory = Object.create(null);

    function get(key, fallback) {
        try {
            const value = localStorage.getItem(key);
            return value === null ? fallback : value;
        } catch (e) {
            return Object.prototype.hasOwnProperty.call(memory, key) ? memory[key] : fallback;
        }
    }

    function set(key, value) {
        const normalized = String(value);
        try {
            localStorage.setItem(key, normalized);
        } catch (e) {
            memory[key] = normalized;
        }
    }

    function remove(key) {
        try {
            localStorage.removeItem(key);
        } catch (e) {
            delete memory[key];
        }
    }

    window.ProactiveStorage = { get, set, remove };
})();
