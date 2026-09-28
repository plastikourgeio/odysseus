let activeSessionId = null;
let projects = [];
let activeProject = null;

let wrap = null;
let button = null;
let menu = null;


function ensureStyle() {
    if (document.getElementById("project-context-style")) return;

    const style = document.createElement("style");
    style.id = "project-context-style";

    style.textContent = `
      #project-context-wrap {
        position: relative;
        display: inline-flex;
        align-items: center;
        margin-left: 6px;
      }

      #project-context-btn {
        max-width: 220px;
        height: 25px;
        padding: 0 9px;
        border: 1px solid var(--border, rgba(127,127,127,.25));
        border-radius: 999px;
        background: transparent;
        color: var(--text-secondary, inherit);
        font-size: 11px;
        cursor: pointer;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      #project-context-btn.active {
        border-color: var(--accent, #888);
        color: var(--text, inherit);
      }

      #project-context-btn:hover {
        background: var(--hover-bg, rgba(127,127,127,.10));
      }

      #project-context-menu {
        display: block !important;
        position: absolute;
        top: calc(100% + 6px);
        right: 0;
        z-index: 5000;
        min-width: 260px;
        max-width: 340px;
        padding: 6px;
        border: 1px solid var(--border, rgba(127,127,127,.25));
        border-radius: 9px;
        background: var(--bg, #111);
        box-shadow: 0 8px 28px rgba(0,0,0,.28);
      }

      #project-context-menu.hidden {
        display: none;
      }

      .project-context-heading {
        padding: 5px 7px 7px;
        font-size: 10px;
        opacity: .62;
        text-transform: uppercase;
        letter-spacing: .04em;
      }

      .project-context-item {
        display: block !important;
        box-sizing: border-box;
        width: 100%;
        border: 0;
        border-radius: 6px;
        padding: 7px 9px;
        background: transparent;
        color: inherit;
        text-align: left;
        font: inherit;
        font-size: 12px;
        cursor: pointer;
      }

      .project-context-item:hover {
        background: var(--hover-bg, rgba(127,127,127,.10));
      }

      .project-context-item.current {
        color: var(--accent, inherit);
        font-weight: 600;
      }

      .project-context-separator {
        height: 1px;
        margin: 5px 3px;
        background: var(--border, rgba(127,127,127,.20));
      }

      .project-context-empty {
        padding: 7px 9px;
        opacity: .6;
        font-size: 11px;
      }
    `;

    document.head.appendChild(style);
}


function ensureUI() {
    if (wrap && document.body.contains(wrap)) return true;

    const host = document.querySelector(".chat-meta-overlay");
    if (!host) return false;

    ensureStyle();

    wrap = document.createElement("span");
    wrap.id = "project-context-wrap";

    button = document.createElement("button");
    button.type = "button";
    button.id = "project-context-btn";
    button.textContent = "Project: None";
    button.title = "Project context";

    menu = document.createElement("div");
    menu.id = "project-context-menu";
    menu.className = "hidden";

    wrap.appendChild(button);
    wrap.appendChild(menu);

    const exportWrap = host.querySelector("#export-dropdown-wrap");
    host.insertBefore(wrap, exportWrap || null);

    button.addEventListener("click", (event) => {
        event.stopPropagation();
        menu.classList.toggle("hidden");
    });

    document.addEventListener("click", (event) => {
        if (wrap && !wrap.contains(event.target)) {
            menu.classList.add("hidden");
        }
    });

    return true;
}


async function api(url, options = {}) {
    const response = await fetch(url, {
        credentials: "same-origin",
        ...options,
    });

    let data = null;

    try {
        data = await response.json();
    } catch (_) {}

    if (!response.ok) {
        const error = new Error(
            data?.detail ||
            data?.error ||
            `HTTP ${response.status}`
        );

        error.status = response.status;
        throw error;
    }

    return data;
}


function item(label, handler, current = false) {
    const el = document.createElement("button");

    el.type = "button";
    el.className =
        "project-context-item" +
        (current ? " current" : "");

    el.textContent = label;
    el.addEventListener("click", handler);

    return el;
}


function separator() {
    const el = document.createElement("div");
    el.className = "project-context-separator";
    return el;
}


function render() {
    if (!ensureUI()) return;

    button.textContent = activeProject
        ? `Project: ${activeProject.title}`
        : "Project: None";

    button.title = activeProject
        ? `Active project: ${activeProject.title}`
        : "No project attached to this chat";

    button.classList.toggle(
        "active",
        Boolean(activeProject),
    );

    menu.replaceChildren();

    const heading = document.createElement("div");
    heading.className = "project-context-heading";
    heading.textContent = "Projects";
    menu.appendChild(heading);

    if (!projects.length) {
        const empty = document.createElement("div");
        empty.className = "project-context-empty";
        empty.textContent = "No projects yet";
        menu.appendChild(empty);
    }

    for (const project of projects) {
        const current =
            activeProject?.project_id === project.project_id;

        menu.appendChild(
            item(
                current
                    ? `✓ ${project.title}`
                    : project.title,
                async () => {
                    try {
                        await api(
                            `/api/projects/${
                                encodeURIComponent(project.project_id)
                            }/attach/${
                                encodeURIComponent(activeSessionId)
                            }`,
                            { method: "POST" },
                        );

                        await refreshProjectContextUI(
                            activeSessionId
                        );

                        menu.classList.add("hidden");
                    } catch (error) {
                        console.error(
                            "Project attach failed:",
                            error
                        );
                        alert(
                            "Could not attach project: " +
                            error.message
                        );
                    }
                },
                current,
            )
        );
    }

    menu.appendChild(separator());

    menu.appendChild(
        item("+ New project…", async () => {
            const title = prompt("Project name:");

            if (!title || !title.trim()) return;

            const goal =
                prompt("Project goal (optional):") || "";

            try {
                const created = await api(
                    "/api/projects",
                    {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                        },
                        body: JSON.stringify({
                            title: title.trim(),
                            goal: goal.trim(),
                        }),
                    },
                );

                const projectId =
                    created?.project?.project_id;

                if (!projectId) {
                    throw new Error(
                        "Project was created without an ID"
                    );
                }

                await api(
                    `/api/projects/${
                        encodeURIComponent(projectId)
                    }/attach/${
                        encodeURIComponent(activeSessionId)
                    }`,
                    { method: "POST" },
                );

                await refreshProjectContextUI(
                    activeSessionId
                );

                menu.classList.add("hidden");
            } catch (error) {
                console.error(
                    "Project creation failed:",
                    error
                );
                alert(
                    "Could not create project: " +
                    error.message
                );
            }
        })
    );

    if (activeProject) {
        menu.appendChild(
            item("Detach from this chat", async () => {
                try {
                    await api(
                        `/api/sessions/${
                            encodeURIComponent(activeSessionId)
                        }/detach-project`,
                        { method: "POST" },
                    );

                    await refreshProjectContextUI(
                        activeSessionId
                    );

                    menu.classList.add("hidden");
                } catch (error) {
                    console.error(
                        "Project detach failed:",
                        error
                    );
                    alert(
                        "Could not detach project: " +
                        error.message
                    );
                }
            })
        );
    }
}


export async function refreshProjectContextUI(sessionId) {
    activeSessionId = sessionId || null;

    if (!ensureUI()) return;

    if (!activeSessionId) {
        wrap.hidden = true;
        return;
    }

    wrap.hidden = false;
    button.disabled = true;
    button.textContent = "Project: …";

    try {
        const listed = await api("/api/projects");

        projects = Array.isArray(listed?.projects)
            ? listed.projects
            : [];

        try {
            const current = await api(
                `/api/sessions/${
                    encodeURIComponent(activeSessionId)
                }/project`
            );

            activeProject = current?.project || null;
        } catch (error) {
            if (error.status === 404) {
                activeProject = null;
            } else {
                throw error;
            }
        }

        render();
    } catch (error) {
        console.warn(
            "Project Context UI refresh failed:",
            error
        );

        activeProject = null;
        projects = [];

        render();

        button.title =
            "Project context unavailable: " +
            error.message;
    } finally {
        button.disabled = false;
    }
}


window.projectContextUI = {
    refresh: refreshProjectContextUI,
};
