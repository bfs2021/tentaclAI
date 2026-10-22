# tentaclai – sandbox + service gateway for Claude Code
# Tools: Claude Code, git, python, node, ssh, jq, ripgrep, database clients, gh – docker runs on the hosts over SSH (svc)
FROM node:22-bookworm-slim

ARG CLAUDE_CODE_VERSION=latest

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    # Updates come via image rebuild, not in-place (npm prefix is not persisted)
    DISABLE_AUTOUPDATER=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git jq ripgrep fd-find less nano vim-tiny tmux \
        openssh-client rsync unzip zip procps iputils-ping dnsutils \
        python3 python3-venv python3-pip make \
        postgresql-client mariadb-client sshpass sudo bash-completion \
    && ln -s /usr/bin/fdfind /usr/local/bin/fd \
    # GitHub CLI (public data only – the GitHub service goes through svc)
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod a+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        gh \
    && rm -rf /var/lib/apt/lists/*

# CACHE_BUST (set by tentacontrol on update) forces a fresh npm install of the latest version
ARG CACHE_BUST=0
RUN echo "cache bust: ${CACHE_BUST}" && npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
    && npm cache clean --force

COPY build/entrypoint.sh build/start.sh build/rc-loop build/cc build/svc build/claude-instructions build/tc-scan build/tc-statusline build/tc-connect build/tc-guard /usr/local/bin/
COPY lib/tclib.py /usr/local/lib/tentaclai/tclib.py
COPY build/svc-completion.bash /etc/bash_completion.d/svc
COPY VERSION /etc/tentaclai/VERSION
COPY build/CLAUDE.base.md /etc/tentaclai/CLAUDE.base.md
COPY build/commands/ /etc/tentaclai/commands/
# Users: claude = the agent (no secrets, no keys), tcrun = client tools started by svc (one secret at a time).
# Root holds .env, SSH keys and service files; Claude reaches them only through `svc`/`tc-scan` via sudo.
ARG CLAUDE_UID=10000
RUN groupadd -g ${CLAUDE_UID} claude && useradd -m -d /home/claude -u ${CLAUDE_UID} -g claude -s /bin/bash claude \
    && groupadd -g 10001 tcrun && useradd -m -d /var/lib/tcrun -u 10001 -g tcrun -s /usr/sbin/nologin tcrun \
    && chmod 700 /var/lib/tcrun \
    # env_keep: only these four names – where a request comes from (session, tmux pane), never
    # CLAUDE_CODE_SESSION_ACCESS_TOKEN or anything else of Claude's environment
    && printf '%s\n' 'Defaults:claude !lecture, env_reset, always_set_home' \
        'Defaults:claude env_keep += "CLAUDE_CODE_SESSION_ID CLAUDE_CODE_ENTRYPOINT TMUX TMUX_PANE"' \
        'claude ALL=(root) NOPASSWD: /usr/local/bin/svc, /usr/local/bin/tc-scan' > /etc/sudoers.d/tentaclai \
    && chmod 0440 /etc/sudoers.d/tentaclai && visudo -cq
RUN chmod 0755 /usr/local/bin/entrypoint.sh /usr/local/bin/start.sh /usr/local/bin/rc-loop /usr/local/bin/cc /usr/local/bin/svc /usr/local/bin/claude-instructions /usr/local/bin/tc-scan /usr/local/bin/tc-statusline /usr/local/bin/tc-connect /usr/local/bin/tc-guard \
    # tmux: mouse scrolling, bigger scrollback, truecolor for Claude's TUI, copies (also Claude's own, OSC 52) reach the browser
    # tab completion in interactive shells (Debian ships it disabled in bash.bashrc)
    && printf '%s\n' '[ -n "$PS1" ] && [ -f /usr/share/bash-completion/bash_completion ] && . /usr/share/bash-completion/bash_completion' >> /etc/bash.bashrc \
    && printf '%s\n' 'set -g mouse on' 'set -g history-limit 50000' 'set -g set-clipboard on' 'set -g allow-passthrough on' \
        'set -g default-terminal "tmux-256color"' 'set -ga terminal-overrides ",*:Tc"' > /etc/tmux.conf

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/usr/local/bin/start.sh"]
