(function () {
    "use strict";

    function normalPath(value) {
        if (!value) return "/";
        var path = value.replace(/\/+$/, "");
        return path || "/";
    }

    function markActiveNavigation() {
        var current = normalPath(window.location.pathname);
        document.querySelectorAll(".nav-link[href], .menu-link[href]").forEach(function (link) {
            try {
                var target = normalPath(new URL(link.href, window.location.origin).pathname);
                if (target === "/") return;
                if (current === target || current.indexOf(target + "/") === 0) {
                    link.classList.add("is-active");
                    link.setAttribute("aria-current", "page");
                }
            } catch (_) {
                /* Ignore non-navigation links. */
            }
        });
    }

    function setupMenuState() {
        var button = document.getElementById("menu-button");
        var menu = document.getElementById("menu-dropdown");
        if (!button || !menu) return;

        function sync() {
            var open = menu.classList.contains("show");
            document.body.classList.toggle("menu-open", open);
            button.setAttribute("aria-expanded", open ? "true" : "false");
        }

        button.addEventListener("click", function () {
            window.requestAnimationFrame(sync);
        });

        document.addEventListener("click", function () {
            window.requestAnimationFrame(sync);
        });

        menu.addEventListener("click", function (event) {
            if (event.target.closest("a")) {
                document.body.classList.remove("menu-open");
            }
        });

        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && menu.classList.contains("show")) {
                menu.classList.remove("show");
                document.body.classList.remove("menu-open");
                button.setAttribute("aria-expanded", "false");
                button.focus();
            }
        });
    }

    function syncThemeColor() {
        var html = document.documentElement;
        var theme = (html.dataset.theme || "system").toLowerCase();
        var dark = theme === "dark" || (
            theme === "system" &&
            window.matchMedia &&
            window.matchMedia("(prefers-color-scheme: dark)").matches
        );
        var meta = document.querySelector('meta[name="theme-color"]');
        if (meta) meta.setAttribute("content", dark ? "#0f172a" : "#172554");
    }

    document.addEventListener("DOMContentLoaded", function () {
        markActiveNavigation();
        setupMenuState();
        syncThemeColor();
    });

    if (window.matchMedia) {
        var media = window.matchMedia("(prefers-color-scheme: dark)");
        if (media.addEventListener) media.addEventListener("change", syncThemeColor);
    }
})();
