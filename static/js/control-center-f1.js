(function () {
    "use strict";

    function normalPath(value) {
        if (!value) return "/";
        var path = value.replace(/\/+$/, "");
        return path || "/";
    }

    function markActive() {
        var current = normalPath(
            window.location.pathname
        );

        document
            .querySelectorAll(
                "#control-nav a[href]"
            )
            .forEach(function (link) {
                var target = normalPath(
                    new URL(
                        link.href,
                        window.location.origin
                    ).pathname
                );

                if (target === "/") {
                    return;
                }

                if (
                    current === target
                    || current.indexOf(
                        target + "/"
                    ) === 0
                ) {
                    link.classList.add(
                        "is-active"
                    );

                    link.setAttribute(
                        "aria-current",
                        "page"
                    );
                }
            });
    }

    function setupNav() {
        var button = document.getElementById(
            "control-nav-toggle"
        );

        var nav = document.getElementById(
            "control-nav"
        );

        var backdrop = document.getElementById(
            "control-nav-backdrop"
        );

        if (!button || !nav) {
            return;
        }

        function isOpen() {
            return nav.classList.contains(
                "is-open"
            );
        }

        function setOpen(
            open,
            focusButton
        ) {
            nav.classList.toggle(
                "is-open",
                open
            );

            nav.setAttribute(
                "aria-hidden",
                open ? "false" : "true"
            );

            button.setAttribute(
                "aria-expanded",
                open ? "true" : "false"
            );

            document.body.classList.toggle(
                "cc-nav-open",
                open
            );

            if (backdrop) {
                backdrop.classList.toggle(
                    "is-open",
                    open
                );

                backdrop.setAttribute(
                    "aria-hidden",
                    open ? "false" : "true"
                );
            }

            if (
                !open
                && focusButton
                && typeof button.focus === "function"
            ) {
                button.focus();
            }
        }

        function closeNav(
            focusButton
        ) {
            if (!isOpen()) {
                return;
            }

            setOpen(
                false,
                Boolean(focusButton)
            );
        }

        function targetIsInsideNav(
            target
        ) {
            return Boolean(
                target
                && (
                    nav.contains(target)
                    || button.contains(target)
                )
            );
        }

        button.addEventListener(
            "click",
            function (event) {
                event.stopPropagation();

                setOpen(
                    !isOpen(),
                    false
                );
            }
        );

        if (backdrop) {
            backdrop.addEventListener(
                "click",
                function () {
                    closeNav(true);
                }
            );
        }

        nav.addEventListener(
            "click",
            function (event) {
                if (
                    event.target.closest("a")
                    || event.target.closest(
                        ".cc-drawer-logout button"
                    )
                ) {
                    closeNav(false);
                }
            }
        );

        /*
         * Any pointer interaction outside the drawer closes it.
         * This includes mouse click and touch press on page content.
         */
        document.addEventListener(
            "pointerdown",
            function (event) {
                if (
                    isOpen()
                    && !targetIsInsideNav(
                        event.target
                    )
                ) {
                    closeNav(false);
                }
            },
            true
        );

        /*
         * Mouse wheel / trackpad scroll outside the drawer:
         * close immediately before the page continues scrolling.
         *
         * Scrolling INSIDE the drawer is intentionally preserved.
         */
        document.addEventListener(
            "wheel",
            function (event) {
                if (
                    isOpen()
                    && !nav.contains(
                        event.target
                    )
                ) {
                    closeNav(false);
                }
            },
            {
                passive: true,
                capture: true
            }
        );

        /*
         * Touch-scroll outside the drawer:
         * close menu, but let the page keep scrolling naturally.
         */
        document.addEventListener(
            "touchmove",
            function (event) {
                if (
                    isOpen()
                    && !nav.contains(
                        event.target
                    )
                ) {
                    closeNav(false);
                }
            },
            {
                passive: true,
                capture: true
            }
        );

        /*
         * Keyboard/page scrolling can move the main document without
         * a pointer event. Window scroll closes the open drawer.
         *
         * Drawer-only scrolling does not scroll window, so it remains open.
         */
        window.addEventListener(
            "scroll",
            function () {
                if (isOpen()) {
                    closeNav(false);
                }
            },
            {
                passive: true
            }
        );

        /*
         * Common navigation keys should also dismiss the menu if focus
         * is not inside it.
         */
        document.addEventListener(
            "keydown",
            function (event) {
                if (
                    event.key === "Escape"
                    && isOpen()
                ) {
                    closeNav(true);
                    return;
                }

                if (
                    isOpen()
                    && !nav.contains(
                        document.activeElement
                    )
                    && (
                        event.key === "PageDown"
                        || event.key === "PageUp"
                        || event.key === "Home"
                        || event.key === "End"
                        || event.key === "ArrowDown"
                        || event.key === "ArrowUp"
                        || event.key === " "
                    )
                ) {
                    closeNav(false);
                }
            }
        );

        /*
         * If viewport changes (rotation, resize, dev-tools responsive
         * switch), close the drawer and let the new layout settle cleanly.
         */
        window.addEventListener(
            "resize",
            function () {
                if (isOpen()) {
                    closeNav(false);
                }
            },
            {
                passive: true
            }
        );
    }

    document.addEventListener(
        "DOMContentLoaded",
        function () {
            markActive();
            setupNav();
        }
    );
})();
