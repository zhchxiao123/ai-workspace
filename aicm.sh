#!/usr/bin/env bash
# ============================================================
#  aicm.sh — AI Code Manager
#  统一管理 Codex CLI 和 Claude Code 的多账号容器环境
# ============================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CONFIG_CONF="$SCRIPT_DIR/config.conf"
ACCOUNTS_CONF="$SCRIPT_DIR/accounts.conf"
PROJECTS_CONF="$SCRIPT_DIR/projects.conf"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
ACCOUNTS_DIR="$SCRIPT_DIR/accounts"
TASKS_DIR="$SCRIPT_DIR/tasks"
DOCKERFILE="$SCRIPT_DIR/Dockerfile"

# ── 颜色 ────────────────────────────────────────────────────
C_CYAN='\033[36m'; C_GREEN='\033[32m'; C_YELLOW='\033[33m'
C_RED='\033[31m';  C_BOLD='\033[1m';   C_DIM='\033[2m'; C_RESET='\033[0m'

info()    { echo -e "${C_CYAN}▶ $*${C_RESET}"; }
success() { echo -e "${C_GREEN}✓ $*${C_RESET}"; }
warn()    { echo -e "${C_YELLOW}⚠ $*${C_RESET}"; }
error()   { echo -e "${C_RED}✗ $*${C_RESET}" >&2; }
bold()    { echo -e "${C_BOLD}$*${C_RESET}"; }
dim()     { echo -e "${C_DIM}$*${C_RESET}"; }
die()     { error "$*"; exit 1; }

# ── docker compose 自动检测 ──────────────────────────────────
if docker compose version &>/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose &>/dev/null; then
  DC="docker-compose"
else
  die "找不到 docker compose 或 docker-compose，请先安装 Docker"
fi

# ── AICM 调度服务地址 ────────────────────────────────────────
AICM_API="${AICM_API:-http://localhost:8765}"

# ============================================================
#  配置加载
# ============================================================

load_config() {
  [[ -f "$CONFIG_CONF" ]] || die "找不到 config.conf"
  set -o allexport
  source <(grep -v '^[[:space:]]*#' "$CONFIG_CONF" | grep -v '^[[:space:]]*$')
  set +o allexport
}

# ============================================================
#  accounts.conf 解析
#
#  格式：NAME=<名称>  TYPE=codex|claude  [AUTH=login|env] [ENV_FILE=...] [PROXY=relay|off]
#  输出：每行 5 列（空格分隔）
#    name  type  auth  env_file|-  proxy
# ============================================================

parse_accounts() {
  [[ -f "$ACCOUNTS_CONF" ]] || die "找不到 accounts.conf"

  local line token name type auth env_file proxy
  while IFS= read -r line; do
    line="${line//$'\r'/}"
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line//[[:space:]]/}" ]] && continue

    name=""; type=""; auth="login"; env_file=""; proxy="relay"
    for token in $line; do
      case "$token" in
        NAME=*)    name="${token#NAME=}" ;;
        TYPE=*)    type="${token#TYPE=}" ;;
        AUTH=*)    auth="${token#AUTH=}" ;;
        ENV_FILE=*) env_file="${token#ENV_FILE=}" ;;
        PROXY=*)   proxy="${token#PROXY=}" ;;
      esac
    done

    name="${name//$'\r'/}"
    type="${type//$'\r'/}"
    auth="${auth//$'\r'/}"
    env_file="${env_file//$'\r'/}"
    proxy="${proxy//$'\r'/}"

    [[ -z "$name" || -z "$type" ]] && {
      warn "跳过无效行（NAME/TYPE 均为必填）：$line"
      continue
    }

    [[ "$name" =~ ^[a-zA-Z0-9-]+$ ]] || {
      warn "NAME='$name' 含非法字符（只允许字母/数字/连字符），跳过"
      continue
    }

    [[ "$type" == "codex" || "$type" == "claude" ]] || {
      warn "TYPE='$type' 不合法（只支持 codex / claude），跳过"
      continue
    }

    [[ "$auth" == "login" || "$auth" == "env" ]] || {
      warn "AUTH='$auth' 不合法（只支持 login / env），跳过"
      continue
    }
    [[ "$proxy" == "relay" || "$proxy" == "off" ]] || {
      warn "PROXY='$proxy' 不合法（只支持 relay / off），跳过"
      continue
    }

    if [[ "$auth" == "env" ]]; then
      [[ "$type" == "claude" ]] || {
        warn "账号 '$name' 使用 AUTH=env，但当前只支持 Claude Code 环境变量认证，跳过"
        continue
      }
      [[ -n "$env_file" ]] || env_file="./accounts/${name}/env"
    fi

    [[ -n "$env_file" ]] || env_file="-"
    printf '%s %s %s %s %s\n' "$name" "$type" "$auth" "$env_file" "$proxy"
  done < "$ACCOUNTS_CONF"
}

get_names() { parse_accounts | awk '{print $1}'; }

# ============================================================
#  projects.conf 解析
#
#  格式：NAME=<名称> ACCOUNT=<账号名> PATH=<宿主机路径>
#  输出：每行 3 列（空格分隔）
#    name  account  /expanded/path
# ============================================================

parse_projects() {
  local line token name account path expanded
  if [[ -f "$PROJECTS_CONF" ]]; then
    while IFS= read -r line; do
      line="${line//$'\r'/}"
      [[ "$line" =~ ^[[:space:]]*# ]] && continue
      [[ -z "${line//[[:space:]]/}" ]] && continue

      name=""; account=""; path=""
      for token in $line; do
        case "$token" in
          NAME=*)    name="${token#NAME=}" ;;
          ACCOUNT=*) account="${token#ACCOUNT=}" ;;
          PATH=*)    path="${token#PATH=}" ;;
        esac
      done

      [[ -z "$name" || -z "$account" || -z "$path" ]] && {
        warn "跳过无效项目行（NAME/ACCOUNT/PATH 均为必填）：$line"
        continue
      }
      expanded="${path/#\~/$HOME}"
      printf '%s %s %s\n' "$name" "$account" "$expanded"
    done < "$PROJECTS_CONF"
  fi

}

# 容器名：TYPE 作前缀（参数：项目名 类型）
container_name() {
  local name="$1" type="$2"
  printf '%s-%s' "$type" "$name"
}

# compose 服务名（参数：项目名 类型）
service_name() {
  local name="$1" type="$2"
  printf '%s-project-%s' "$type" "$name"
}

# 临时登录容器名（参数：账号名 类型）
container_name_for_account() {
  local name="$1" type="$2"
  printf 'aicm-login-%s-%s' "$type" "$name"
}

# 认证目录挂载路径（容器内固定路径）
auth_mount_target() {
  local type="$1"
  case "$type" in
    codex)  echo "/home/byclaw/.codex" ;;
    claude) echo "/home/byclaw/.claude" ;;
  esac
}

is_running() {
  local ctr="$1"
  local state
  state=$(docker inspect "$ctr" --format '{{.State.Running}}' 2>/dev/null) || true
  [[ "$state" == "true" ]]
}

require_running() {
  local ctr="$1"
  is_running "$ctr" || die "容器 $ctr 未运行，请先执行：./aicm.sh up"
}

# CLI 二进制名
cli_bin() {
  local type="$1"
  case "$type" in
    codex)  echo "codex" ;;
    claude) echo "claude" ;;
  esac
}

login_args() {
  local type="$1"
  case "$type" in
    codex)  printf '%s\n' login --device-auth ;;
    claude) printf '%s\n' login ;;
  esac
}

image_ref() {
  load_config
  printf '%s:%s' "${IMAGE_NAME:-ai-code-manager}" "${IMAGE_TAG:-latest}"
}

# ============================================================
#  构建镜像
# ============================================================

cmd_build() {
  [[ -f "$DOCKERFILE" ]] || die "找不到 Dockerfile"
  load_config

  local image="${IMAGE_NAME:-ai-code-manager}:${IMAGE_TAG:-latest}"
  local platform="${BUILD_PLATFORM:-linux/amd64}"

  info "构建镜像 ${image}（平台：${platform}）..."
  info "首次构建约需 5~10 分钟，请耐心等待"
  echo ""

  docker build \
    --platform "$platform" \
    --tag "$image" \
    --file "$DOCKERFILE" \
    "$SCRIPT_DIR"

  echo ""
  success "镜像构建完成：$image"
}

# ============================================================
#  生成 docker-compose.yml
# ============================================================

generate_compose() {
  load_config

  local projects count
  projects="$(parse_projects)"
  count="$(printf '%s\n' "$projects" | grep -c . 2>/dev/null)" || count=0
  [[ "$count" -eq 0 ]] && die "projects.conf 中没有有效项目"

  local image="${IMAGE_NAME:-ai-code-manager}:${IMAGE_TAG:-latest}"
  local subnet="${INTERNAL_SUBNET:-172.21.0.0/16}"
  local relay_ip="${RELAY_IP:-172.21.0.2}"
  local relay_listen_port="${RELAY_LISTEN_PORT:-7890}"
  local proxy_host="${PROXY_HOST:-host.docker.internal}"
  local http_port="${PROXY_HTTP_PORT:-7890}"
  local socks_port="${PROXY_SOCKS5_PORT:-7891}"
  local relay_image="${RELAY_IMAGE:-gogost/gost:3}"

  local proxy_url="http://${relay_ip}:${relay_listen_port}"
  local no_proxy="localhost,127.0.0.1,${subnet}"

  info "读取到 $count 个项目，生成 docker-compose.yml..."

  # ── 文件头（单引号 heredoc，变量不展开）─────────────────
  cat > "$COMPOSE_FILE" << 'HEADER'
# !! 此文件由 ./aicm.sh apply 自动生成，请勿手动编辑 !!

networks:
  intnet:
    driver: bridge
    internal: true
    ipam:
      config:
        - subnet: __SUBNET__
  extnet:
    driver: bridge

services:

  proxy-relay:
    image: __RELAY_IMAGE__
    container_name: aicm-proxy-relay
    restart: unless-stopped
    networks:
      intnet:
        ipv4_address: __RELAY_IP__
      extnet: {}
    extra_hosts:
      - "host.docker.internal:host-gateway"
    command: >
      -L http://:__RELAY_LISTEN_PORT__
      -F "__PROXY_HOST__:__HTTP_PORT__"
    healthcheck:
      test: ["CMD", "sh", "-c", "nc -z localhost __RELAY_LISTEN_PORT__"]
      interval: 8s
      timeout: 4s
      retries: 5
      start_period: 5s

HEADER

  # python3 替换占位符（不受 bash 变量展开限制）
  python3 - "$COMPOSE_FILE" \
    "$subnet" "$relay_ip" "$relay_listen_port" \
    "$proxy_host" "$http_port" \
    "$relay_image" << 'PYEOF'
import sys
f, subnet, relay_ip, relay_listen_port, proxy_host, http_port, relay_image = sys.argv[1:]
txt = open(f).read()
txt = txt.replace('__SUBNET__',            subnet)
txt = txt.replace('__RELAY_IP__',          relay_ip)
txt = txt.replace('__RELAY_LISTEN_PORT__', relay_listen_port)
txt = txt.replace('__PROXY_HOST__',        proxy_host)
txt = txt.replace('__HTTP_PORT__',         http_port)
txt = txt.replace('__RELAY_IMAGE__',       relay_image)
open(f, 'w').write(txt)
PYEOF

  # ── 逐个写入项目服务 ─────────────────────────────────────
  local project_name project_account project_path
  local acc_type acc_auth acc_env_file acc_proxy svc ctr auth_src auth_dst
  local env_file_block network_block proxy_env_block depends_block relay_env_block

  while IFS=' ' read -r project_name project_account project_path; do
    # 通过 project.account 查找关联的 type
    acc_type=$(parse_accounts | awk -v n="$project_account" '$1==n{print $2; exit}')
    acc_auth=$(parse_accounts | awk -v n="$project_account" '$1==n{print $3; exit}')
    acc_env_file=$(parse_accounts | awk -v n="$project_account" '$1==n{print $4; exit}')
    acc_proxy=$(parse_accounts | awk -v n="$project_account" '$1==n{print $5; exit}')
    [[ -z "$acc_type" ]] && { warn "跳过项目 ${project_name}：账号 ${project_account} 不存在"; continue; }
    acc_auth="${acc_auth:-login}"
    acc_proxy="${acc_proxy:-relay}"

    svc="$(service_name "$project_name" "$acc_type")"
    ctr="$(container_name "$project_name" "$acc_type")"
    auth_src="./accounts/${project_account}"
    auth_dst="$(auth_mount_target "$acc_type")"
    mkdir -p "$ACCOUNTS_DIR/$project_account"

    env_file_block=""
    if [[ "$acc_auth" == "env" ]]; then
      env_file_block="    env_file:
      - ${acc_env_file}"
      [[ -f "$SCRIPT_DIR/${acc_env_file#./}" || -f "$acc_env_file" ]] || \
        warn "账号 ${project_account} 的 ENV_FILE 不存在：${acc_env_file}"
    fi

    if [[ "$acc_proxy" == "off" ]]; then
      network_block="      extnet: {}"
      proxy_env_block=""
      relay_env_block=""
      depends_block=""
    else
      network_block="      intnet: {}"
      proxy_env_block="      HTTP_PROXY:  \"${proxy_url}\"
      HTTPS_PROXY: \"${proxy_url}\"
      http_proxy:  \"${proxy_url}\"
      https_proxy: \"${proxy_url}\"
      ALL_PROXY:   \"${proxy_url}\"
      all_proxy:   \"${proxy_url}\"
      NO_PROXY:    \"${no_proxy}\"
      no_proxy:    \"${no_proxy}\""
      relay_env_block="      AICM_RELAY_IP:     \"${relay_ip}\"
      AICM_RELAY_PORT:   \"${relay_listen_port}\""
      depends_block="    depends_on:
      proxy-relay:
        condition: service_healthy"
    fi

    cat >> "$COMPOSE_FILE" << EOF
  ${svc}:
    image: ${image}
    platform: ${BUILD_PLATFORM}
    pull_policy: never
    container_name: ${ctr}
    restart: unless-stopped
    networks:
${network_block}
    environment:
${proxy_env_block}
      CODEX_HOME: "/home/byclaw/.codex"
      CLAUDE_CONFIG_DIR: "/home/byclaw/.claude"
      AICM_ACCOUNT_NAME: "${project_account}"
      AICM_ACCOUNT_TYPE: "${acc_type}"
      AICM_ACCOUNT_AUTH: "${acc_auth}"
      AICM_ACCOUNT_PROXY: "${acc_proxy}"
${relay_env_block}
${env_file_block}
    volumes:
      - ${auth_src}:${auth_dst}
      - ${project_path}:/workspace
    working_dir: /workspace
${depends_block}

EOF
    success "  [${acc_type}] ${project_name}（账号：${project_account}，账号代理：${acc_proxy}）"
  done <<< "$projects"

  success "docker-compose.yml 生成完成（共 $count 个项目）"
}

# ============================================================
#  命令实现
# ============================================================

cmd_help() {
  bold ""
  bold "  aicm — AI Code Manager"
  dim "  统一管理 Codex CLI 和 Claude Code 的多账号容器环境"
  echo ""
  echo -e "  ${C_YELLOW}镜像管理${C_RESET}"
  echo "    ./aicm.sh build              构建自定义镜像（首次使用必须执行）"
  echo ""
  echo -e "  ${C_YELLOW}账号管理${C_RESET}"
  echo "    ./aicm.sh account add <名称> <TYPE=codex|claude> [AUTH=login|env] [ENV_FILE=路径] [PROXY=relay|off]"
  echo "    ./aicm.sh account remove <名称>"
  echo "    ./aicm.sh account list"
  echo "    ./aicm.sh project add <名称> <账号名> <项目路径>"
  echo "    ./aicm.sh project remove <名称>"
  echo "    ./aicm.sh project list"
  echo ""
  echo -e "  ${C_YELLOW}容器操作${C_RESET}"
  echo "    ./aicm.sh apply              重新生成配置并重启所有容器"
  echo "    ./aicm.sh up                 启动所有容器"
  echo "    ./aicm.sh down               停止所有容器"
  echo "    ./aicm.sh restart            重启所有容器"
  echo "    ./aicm.sh status             查看状态"
  echo "    ./aicm.sh logs [项目名]      查看日志"
  echo ""
  echo -e "  ${C_YELLOW}账号操作${C_RESET}"
  echo "    ./aicm.sh enter <项目名>     进入项目容器 shell"
  echo "    ./aicm.sh login <账号名|all> 登录并持久化认证文件"
  echo ""
  echo -e "  ${C_YELLOW}任务管理（需先启动调度服务）${C_RESET}"
  echo "    ./aicm.sh task run <描述> --project <项目名|路径>            一次性任务"
  echo "    ./aicm.sh task run <描述> --project <名> --new-chain <链名>  新建任务链"
  echo "    ./aicm.sh task run <描述> --conversation <ID>                续接任务链"
  echo "    ./aicm.sh task list          列出所有任务"
  echo "    ./aicm.sh task logs <ID> -f  实时跟踪日志"
  echo "    ./aicm.sh task help          查看完整任务命令"
  echo ""
  echo -e "  ${C_YELLOW}诊断${C_RESET}"
  echo "    ./aicm.sh check-proxy        验证代理隔离"
  echo ""
  echo -e "  ${C_YELLOW}调度服务${C_RESET}"
  echo "    ./aicm.sh server             启动 FastAPI 调度服务（默认端口 8765）"
  echo ""
  echo -e "  ${C_YELLOW}快速开始${C_RESET}"
  echo "    ./aicm.sh build"
  echo "    ./aicm.sh account add alice TYPE=codex"
  echo "    ./aicm.sh account add bob   TYPE=claude"
  echo "    ./aicm.sh account add api   TYPE=claude AUTH=env PROXY=off"
  echo "    ./aicm.sh project add app-a alice ~/projects/app-a"
  echo "    ./aicm.sh project add app-b bob   ~/projects/app-b"
  echo "    ./aicm.sh apply"
  echo "    ./aicm.sh login all"
  echo "    ./aicm.sh server &           # 后台启动调度服务"
  echo "    ./aicm.sh enter app-a        # 进入项目 app-a"
  echo ""
}

cmd_account() {
  local sub="${1:-}"
  sub="${sub//$'\r'/}"
  shift || true

  case "$sub" in
    add)
      local name="${1:-}" typearg="${2:-}"
      name="${name//$'\r'/}"
      typearg="${typearg//$'\r'/}"
      shift 2 || true

      [[ -z "$name" || -z "$typearg" ]] && \
        die "用法：./aicm.sh account add <名称> TYPE=codex|claude [AUTH=login|env] [ENV_FILE=路径] [PROXY=relay|off]"

      [[ "$name" =~ ^[a-zA-Z0-9-]+$ ]] || \
        die "NAME 只能包含字母、数字、连字符"

      # 解析 TYPE=xxx
      local type=""
      case "$typearg" in
        TYPE=codex|TYPE=claude) type="${typearg#TYPE=}" ;;
        codex|claude)           type="$typearg" ;;
        *) die "TYPE 不合法，只支持 TYPE=codex 或 TYPE=claude" ;;
      esac

      local auth="login" env_file="" proxy="relay"
      local opt
      for opt in "$@"; do
        opt="${opt//$'\r'/}"
        case "$opt" in
          AUTH=login|AUTH=env) auth="${opt#AUTH=}" ;;
          ENV_FILE=*) env_file="${opt#ENV_FILE=}" ;;
          PROXY=relay|PROXY=off) proxy="${opt#PROXY=}" ;;
          *) die "未知参数：$opt（支持 AUTH=login|env / ENV_FILE=路径 / PROXY=relay|off；ENV_FILE 可省略，默认 ./accounts/<名称>/env）" ;;
        esac
      done

      if [[ "$auth" == "env" ]]; then
        [[ "$type" == "claude" ]] || die "AUTH=env 目前只支持 TYPE=claude"
        [[ -n "$env_file" ]] || env_file="./accounts/${name}/env"
      fi

      if grep -qE "NAME=${name}([[:space:]]|$)" "$ACCOUNTS_CONF" 2>/dev/null; then
        die "账号 '$name' 已存在，若要修改请先 remove 再 add"
      fi

      if [[ "$auth" == "env" ]]; then
        printf 'NAME=%-20s TYPE=%-8s AUTH=%-6s ENV_FILE=%s PROXY=%s\n' "$name" "$type" "$auth" "$env_file" "$proxy" \
          >> "$ACCOUNTS_CONF"
      else
        printf 'NAME=%-20s TYPE=%-8s AUTH=%-6s PROXY=%s\n' "$name" "$type" "$auth" "$proxy" \
          >> "$ACCOUNTS_CONF"
      fi
      mkdir -p "$ACCOUNTS_DIR/$name"
      if [[ "$auth" == "env" && "$env_file" == ./* ]]; then
        mkdir -p "$(dirname "$SCRIPT_DIR/${env_file#./}")"
      fi

      success "账号 '$name' 已添加（类型：${type}，认证：${auth}，代理：${proxy}）"
      if [[ "$auth" == "env" ]]; then
        info "请在 ${env_file} 中配置 ANTHROPIC_API_KEY 等 Claude Code 环境变量"
      fi
      info "接下来可执行：./aicm.sh project add <项目名> $name <项目路径>"
      warn "执行 ./aicm.sh apply 使配置生效"
      ;;

    remove)
      local name="${1:-}"
      name="${name//$'\r'/}"
      [[ -z "$name" ]] && die "用法：./aicm.sh account remove <名称>"

      if ! grep -qE "NAME=${name}([[:space:]]|$)" "$ACCOUNTS_CONF" 2>/dev/null; then
        die "账号 '$name' 不存在"
      fi

      # 停止并删除该账号关联的所有项目容器
      local type
      type=$(parse_accounts | awk -v n="$name" '$1==n{print $2; exit}')
      if [[ -n "$type" ]]; then
        while IFS=' ' read -r pn pa pp; do
          [[ "$pa" == "$name" ]] || continue
          local ctr svc
          ctr="$(container_name "$pn" "$type")"
          svc="$(service_name "$pn" "$type")"
          if is_running "$ctr"; then
            info "停止容器 $ctr..."
            $DC stop "$svc" 2>/dev/null || true
            $DC rm -f "$svc" 2>/dev/null || true
          fi
        done < <(parse_projects)
      fi

      local tmp
      tmp="$(mktemp)"
      grep -vE "^[[:space:]]*NAME=${name}([[:space:]]|$)" \
        "$ACCOUNTS_CONF" > "$tmp" || true
      mv "$tmp" "$ACCOUNTS_CONF"

      success "账号 '$name' 已从配置中移除"
      warn "认证数据保留在 accounts/${name}/  如需清除：rm -rf accounts/${name}"
      warn "执行 ./aicm.sh apply 重新生成配置"
      ;;

    list)
      bold ""
      bold "  ── 账号列表 ─────────────────────────────────────────────"
      printf "  ${C_BOLD}%-20s %-8s %-8s %-12s  %s${C_RESET}\n" \
        "名称" "类型" "认证" "状态" "项目数"
      printf "  %s\n" "$(printf '─%.0s' {1..70})"

      local count=0 name type auth env_file proxy status project_count
      while IFS=' ' read -r name type auth env_file proxy; do
        # 检查该账号是否有任何项目容器在运行
        local any_running=false
        while IFS=' ' read -r pn pa pp; do
          [[ "$pa" == "$name" ]] || continue
          local ctr
          ctr="$(container_name "$pn" "$type")"
          if is_running "$ctr"; then any_running=true; break; fi
        done < <(parse_projects)
        if $any_running; then
          status="${C_GREEN}● 运行中${C_RESET}"
        else
          status="${C_YELLOW}○ 已停止${C_RESET}"
        fi
        project_count="$(parse_projects | awk -v a="$name" '$2==a{c++} END{print c+0}')"
        printf "  %-20s %-8s %-8s %-20b  %s（代理：%s）\n" "$name" "$type" "$auth" "$status" "$project_count" "$proxy"
        (( count++ )) || true
      done < <(parse_accounts)
      echo ""
      [[ $count -eq 0 ]] && warn "暂无账号，使用 ./aicm.sh account add 添加"
      return 0
      ;;

    *)
      error "未知子命令：$sub"
      echo "  可用子命令：add / remove / list"
      exit 1
      ;;
  esac
}

cmd_project() {
  local sub="${1:-}"
  sub="${sub//$'\r'/}"
  shift || true

  case "$sub" in
    add)
      local name="${1:-}" account="${2:-}" path="${3:-}"
      name="${name//$'\r'/}"
      account="${account//$'\r'/}"
      path="${path//$'\r'/}"
      shift 3 || true

      [[ -z "$name" || -z "$account" || -z "$path" ]] && \
        die "用法：./aicm.sh project add <名称> <账号名> <项目路径>"
      [[ "$#" -eq 0 ]] || die "未知参数：$*（项目代理已移至账号配置，请使用 account add ... PROXY=relay|off）"
      [[ "$name" =~ ^[a-zA-Z0-9-]+$ ]] || \
        die "项目名称只能包含字母、数字、连字符"
      parse_accounts | awk -v n="$account" '$1==n{found=1} END{exit found?0:1}' || \
        die "账号 '$account' 不存在"
      if [[ -f "$PROJECTS_CONF" ]] && grep -qE "NAME=${name}([[:space:]]|$)" "$PROJECTS_CONF"; then
        die "项目 '$name' 已存在，若要修改请先 remove 再 add"
      fi

      local expanded="${path/#\~/$HOME}"
      printf 'NAME=%-20s ACCOUNT=%-20s PATH=%s\n' "$name" "$account" "$path" \
        >> "$PROJECTS_CONF"
      success "项目 '${name}' 已添加（账号：${account}  路径：${expanded}）"
      warn "执行 ./aicm.sh apply 使配置生效"
      ;;

    remove)
      local name="${1:-}"
      name="${name//$'\r'/}"
      [[ -z "$name" ]] && die "用法：./aicm.sh project remove <名称>"
      [[ -f "$PROJECTS_CONF" ]] || die "projects.conf 不存在"
      if ! grep -qE "NAME=${name}([[:space:]]|$)" "$PROJECTS_CONF"; then
        die "项目 '$name' 不存在"
      fi
      local tmp
      tmp="$(mktemp)"
      grep -vE "^[[:space:]]*NAME=${name}([[:space:]]|$)" "$PROJECTS_CONF" > "$tmp" || true
      mv "$tmp" "$PROJECTS_CONF"
      success "项目 '$name' 已移除"
      warn "执行 ./aicm.sh apply 重新生成配置"
      ;;

    list)
      bold ""
      bold "  ── 项目列表 ─────────────────────────────────────────────"
      printf "  ${C_BOLD}%-20s %-20s  %s${C_RESET}\n" "名称" "账号" "路径"
      printf "  %s\n" "$(printf '─%.0s' {1..74})"
      local count=0 name account path
      while IFS=' ' read -r name account path; do
        printf "  %-20s %-20s  %s\n" "$name" "$account" "$path"
        (( count++ )) || true
      done < <(parse_projects)
      echo ""
      [[ $count -eq 0 ]] && warn "暂无项目，使用 ./aicm.sh project add 添加"
      return 0
      ;;

    *)
      error "未知子命令：$sub"
      echo "  可用子命令：add / remove / list"
      exit 1
      ;;
  esac
}

cmd_apply() {
  generate_compose
  echo ""
  info "重启容器以应用新配置..."
  $DC down --remove-orphans 2>/dev/null || true
  $DC up -d
  echo ""
  success "完成！使用 ./aicm.sh status 查看状态"
}

cmd_up() {
  [[ -f "$COMPOSE_FILE" ]] || {
    warn "docker-compose.yml 不存在，先生成..."
    generate_compose
    echo ""
  }
  info "启动所有容器..."
  $DC up -d
  success "启动完成"
}

cmd_down() {
  info "停止所有容器..."
  $DC down
  success "已停止"
}

cmd_restart() {
  info "重启..."
  $DC down
  $DC up -d
  success "重启完成"
}

cmd_status() {
  bold ""
  bold "  ── 项目与容器状态 ──────────────────────────────────────"
  local project_name project_account project_path acc_type ctr status

  while IFS=' ' read -r project_name project_account project_path; do
    acc_type=$(parse_accounts | awk -v n="$project_account" '$1==n{print $2; exit}')
    ctr="$(container_name "$project_name" "$acc_type")"
    if is_running "$ctr"; then
      status="${C_GREEN}● 运行中${C_RESET}"
    else
      status="${C_RED}○ 已停止${C_RESET}"
    fi
    printf "  %-20s [%-6s] %b  账号：%s\n" "$project_name" "$acc_type" "$status" "$project_account"
  done < <(parse_projects)

  echo ""
  bold "  ── 代理中继 ─────────────────────────────────────────────"
  local health
  health="$(docker inspect aicm-proxy-relay \
    --format '{{.State.Health.Status}}' 2>/dev/null)" || health="未运行"
  case "$health" in
    healthy)  echo -e "  aicm-proxy-relay: ${C_GREEN}${health}${C_RESET}" ;;
    starting) echo -e "  aicm-proxy-relay: ${C_YELLOW}${health}（启动中）${C_RESET}" ;;
    *)        echo -e "  aicm-proxy-relay: ${C_RED}${health}${C_RESET}" ;;
  esac
  echo ""

  bold "  ── 镜像信息 ─────────────────────────────────────────────"
  load_config
  local image="${IMAGE_NAME:-ai-code-manager}:${IMAGE_TAG:-latest}"
  if docker image inspect "$image" &>/dev/null; then
    local created size
    created=$(docker image inspect "$image" --format '{{.Created}}' | cut -c1-10)
    size=$(docker image inspect "$image" --format '{{.Size}}' \
      | awk '{printf "%.1f GB", $1/1024/1024/1024}')
    echo -e "  ${image}: ${C_GREEN}已构建${C_RESET}（创建于 ${created}，大小 ${size}）"
  else
    echo -e "  $image: ${C_RED}未构建${C_RESET}，请执行 ./aicm.sh build"
  fi
  echo ""
}

cmd_logs() {
  local target="${1:-}"
  target="${target//$'\r'/}"
  if [[ -z "$target" ]]; then
    $DC logs -f
  else
    local pline acc_type
    pline="$(project_line_by_name "$target")"
    [[ -z "$pline" ]] && die "项目 '$target' 不在 projects.conf 中"
    acc_type=$(parse_accounts | awk -v n="$(echo "$pline" | awk '{print $2}')" '$1==n{print $2; exit}')
    $DC logs -f "$(service_name "$target" "$acc_type")"
  fi
}

cmd_enter() {
  local name="${1:-}"
  name="${name//$'\r'/}"
  [[ -z "$name" ]] && die "用法：./aicm.sh enter <项目名>"

  local pline acc_type
  pline="$(project_line_by_name "$name")"
  [[ -z "$pline" ]] && die "项目 '$name' 不在 projects.conf 中"
  acc_type=$(parse_accounts | awk -v n="$(echo "$pline" | awk '{print $2}')" '$1==n{print $2; exit}')

  local ctr
  ctr="$(container_name "$name" "$acc_type")"
  require_running "$ctr"

  info "进入 ${ctr}（类型：${acc_type}）..."
  info "可用命令：$(cli_bin "$acc_type")"
  docker exec -it "$ctr" bash
}

cmd_login() {
  local target="${1:-}"
  target="${target//$'\r'/}"
  [[ -z "$target" ]] && die "用法：./aicm.sh login <账号名|all>"

  if [[ "$target" == "all" ]]; then
    local names total i=1 name
    names="$(get_names)"
    total="$(printf '%s\n' "$names" | grep -c . 2>/dev/null)" || total=0
    while IFS= read -r name; do
      echo ""
      bold "  ── [${i}/${total}] 账号：${name} ──────────────────"
      cmd_login "$name"
      (( i++ )) || true
    done <<< "$names"
    echo ""
    success "所有账号登录完成"
    return
  fi

  local type auth env_file
  type=$(parse_accounts | awk -v n="$target" '$1==n{print $2; exit}')
  auth=$(parse_accounts | awk -v n="$target" '$1==n{print $3; exit}')
  env_file=$(parse_accounts | awk -v n="$target" '$1==n{print $4; exit}')
  [[ -z "$type" ]] && die "账号 '$target' 不在 accounts.conf 中"
  auth="${auth:-login}"

  if [[ "$auth" == "env" ]]; then
    info "账号 ${target} 使用环境变量认证（ENV_FILE=${env_file}），无需执行交互登录"
    warn "Claude Code 会优先使用 ANTHROPIC_API_KEY，并可能按 API 计费；请用 /status 确认当前认证方式"
    return 0
  fi

  local cli login_cmd running_ctr=''
  cli="$(cli_bin "$type")"
  login_cmd="$(login_args "$type")"

  # 找一个该账号下正在运行的项目容器
  while IFS=' ' read -r pn pa pp; do
    [[ "$pa" == "$target" ]] || continue
    local candidate
    candidate="$(container_name "$pn" "$type")"
    if is_running "$candidate"; then
      running_ctr="$candidate"
      break
    fi
  done < <(parse_projects)

  info "登录账号 ${target}（类型：${type}）..."
  echo -e "  ${C_YELLOW}提示：浏览器打开授权 URL → 复制 code → 粘贴回来${C_RESET}"
  echo ""

  if [[ -n "$running_ctr" ]]; then
    docker exec -it "$running_ctr" "$cli" $login_cmd
    return $?
  fi

  load_config
  local image platform proxy_host proxy_port proxy_url auth_src auth_dst login_ctr
  image="$(image_ref)"
  platform="${BUILD_PLATFORM:-linux/amd64}"
  proxy_host="${PROXY_HOST:-host.docker.internal}"
  proxy_port="${PROXY_HTTP_PORT:-7890}"
  proxy_url="http://${proxy_host}:${proxy_port}"
  auth_src="$ACCOUNTS_DIR/$target"
  auth_dst="$(auth_mount_target "$type")"
  login_ctr="$(container_name_for_account "$target" "$type")"

  docker image inspect "$image" >/dev/null 2>&1 || \
    die "镜像 $image 不存在，请先执行：./aicm.sh build"

  mkdir -p "$auth_src"
  docker rm -f "$login_ctr" >/dev/null 2>&1 || true

  info "账号容器未运行，启动临时认证容器 ${login_ctr}..."
  docker run --rm -it \
    --name "$login_ctr" \
    --platform "$platform" \
    --add-host host.docker.internal:host-gateway \
    -e HTTP_PROXY="$proxy_url" \
    -e HTTPS_PROXY="$proxy_url" \
    -e http_proxy="$proxy_url" \
    -e https_proxy="$proxy_url" \
    -e ALL_PROXY="$proxy_url" \
    -e all_proxy="$proxy_url" \
    -e NO_PROXY="localhost,127.0.0.1" \
    -e no_proxy="localhost,127.0.0.1" \
    -e CODEX_HOME="/home/byclaw/.codex" \
    -e CLAUDE_CONFIG_DIR="/home/byclaw/.claude" \
    -e AICM_ACCOUNT_NAME="$target" \
    -e AICM_ACCOUNT_TYPE="$type" \
    -e AICM_RELAY_IP="$proxy_host" \
    -e AICM_RELAY_PORT="$proxy_port" \
    -v "${auth_src}:${auth_dst}" \
    -w /workspace \
    "$image" "$cli" $login_cmd
}

cmd_check_proxy() {
  load_config
  local relay_ip="${RELAY_IP:-172.21.0.2}"
  local relay_listen_port="${RELAY_LISTEN_PORT:-7890}"
  local project_name project_account project_path acc_type ctr ip

  bold ""
  bold "  ── 代理连通性（应全部通）─────────────────────────"
  while IFS=' ' read -r project_name project_account project_path; do
    acc_type=$(parse_accounts | awk -v n="$project_account" '$1==n{print $2; exit}')
    ctr="$(container_name "$project_name" "$acc_type")"
    printf '  %-28s → proxy-relay: ' "$ctr"
    if ! is_running "$ctr"; then warn "容器未运行"; continue; fi
    if docker exec "$ctr" \
        sh -c "nc -z ${relay_ip} ${relay_listen_port}" 2>/dev/null; then
      success "通"
    else
      error "不通"
    fi
  done < <(parse_projects)

  echo ""
  bold "  ── 直连公网封锁（应全部封锁）─────────────────────"
  while IFS=' ' read -r project_name project_account project_path; do
    acc_type=$(parse_accounts | awk -v n="$project_account" '$1==n{print $2; exit}')
    ctr="$(container_name "$project_name" "$acc_type")"
    printf '  %-28s → 8.8.8.8:443: ' "$ctr"
    if ! is_running "$ctr"; then warn "容器未运行"; continue; fi
    if docker exec "$ctr" \
        sh -c 'timeout 3 nc -z 8.8.8.8 443 2>/dev/null'; then
      error "可直连 ← 隔离未生效！"
    else
      success "已封锁"
    fi
  done < <(parse_projects)

  echo ""
  bold "  ── 代理出口 IP ─────────────────────────────────────"
  while IFS=' ' read -r project_name project_account project_path; do
    acc_type=$(parse_accounts | awk -v n="$project_account" '$1==n{print $2; exit}')
    ctr="$(container_name "$project_name" "$acc_type")"
    printf '  %-28s 出口 IP: ' "$ctr"
    if ! is_running "$ctr"; then warn "容器未运行"; continue; fi
    ip="$(docker exec "$ctr" \
      sh -c 'curl -s --max-time 6 https://api.ipify.org 2>/dev/null')" || ip=""
    [[ -n "$ip" ]] && echo -e "${C_GREEN}${ip}${C_RESET}" || warn "请求失败"
  done < <(parse_projects)
  echo ""
}

# ============================================================
#  辅助函数
# ============================================================

project_line_by_name() {
  local project_name="$1"
  parse_projects | awk -v n="$project_name" '$1==n{print; exit}'
}

# ============================================================
#  任务管理（通过 AICM 调度服务 API）
# ============================================================

require_server() {
  curl -sf "${AICM_API}/api/health" >/dev/null 2>&1 || \
    die "AICM 服务未启动，请先执行：./aicm.sh server（或 AICM_API=<地址> 指定远端服务）"
}

# 提取 HTTP 响应体和状态码
# 用法：_http_call <method> <url> [--data <body>]
# 输出：设置 _HTTP_BODY 和 _HTTP_CODE 两个变量
_http_call() {
  local method="$1" url="$2"
  local data_flag="" data_body=""
  [[ "${3:-}" == "--data" ]] && { data_flag="-d"; data_body="${4:-}"; }

  local raw
  if [[ -n "$data_flag" ]]; then
    raw=$(curl -s -w '\n__CODE__:%{http_code}' \
      -X "$method" "$url" \
      -H "Content-Type: application/json" \
      "$data_flag" "$data_body")
  else
    raw=$(curl -s -w '\n__CODE__:%{http_code}' -X "$method" "$url")
  fi

  _HTTP_CODE="${raw##*__CODE__:}"
  _HTTP_BODY="${raw%$'\n'__CODE__:*}"
}

# 从 JSON 响应中提取 detail 字段（用于错误提示）
_api_error_detail() {
  python3 -c "
import json, sys
try:
    d = json.loads(sys.argv[1])
    print(d.get('detail', sys.argv[1]))
except Exception:
    print(sys.argv[1])
" "${1:-}"
}

cmd_task() {
  local sub="${1:-}"
  sub="${sub//$'\r'/}"
  shift || true

  case "$sub" in

    # ── run：提交任务 ──────────────────────────────────────
    run)
      require_server
      local prompt="" account="" prefer_type="" prefer_project="" auto=false
      local conversation_id="" conversation_name=""

      while [[ $# -gt 0 ]]; do
        case "$1" in
          --account=*)      account="${1#--account=}" ;;
          --account)        shift; account="${1:-}" ;;
          --type=*)         prefer_type="${1#--type=}" ;;
          --type)           shift; prefer_type="${1:-}" ;;
          --project=*)      prefer_project="${1#--project=}" ;;
          --project)        shift; prefer_project="${1:-}" ;;
          --auto)           auto=true ;;
          --conversation=*) conversation_id="${1#--conversation=}" ;;
          --conversation)   shift; conversation_id="${1:-}" ;;
          --new-chain=*)    conversation_name="${1#--new-chain=}" ;;
          --new-chain)      shift; conversation_name="${1:-}" ;;
          *)                prompt="${prompt:+$prompt }$1" ;;
        esac
        shift || true
      done

      [[ -z "$prompt" ]] && \
        die "用法：./aicm.sh task run <描述> --project <项目名|路径> [--auto]"

      # 构造 JSON 请求体
      # --project 若为绝对路径/相对路径/~ 开头 → project 字段（路径匹配）
      # --project 若为普通字符串              → project_name 字段（名称匹配）
      local body
      body=$(python3 - \
        "$prompt" "$auto" "$prefer_project" \
        "$account" "$prefer_type" \
        "$conversation_id" "$conversation_name" << 'PY'
import json, sys, os
prompt, auto, prefer_project, account, prefer_type, conv_id, conv_name = sys.argv[1:]
d = {"prompt": prompt, "auto": auto == "true"}
if prefer_project:
    exp = os.path.expanduser(prefer_project)
    if os.path.isabs(exp) or prefer_project.startswith("~") or prefer_project.startswith("./"):
        d["project"] = str(os.path.realpath(exp))
    else:
        d["project_name"] = prefer_project
if account:       d["account"]           = account
if prefer_type:   d["type"]              = prefer_type
if conv_id:       d["conversation_id"]   = conv_id
if conv_name:     d["conversation_name"] = conv_name
print(json.dumps(d))
PY
)

      _http_call POST "${AICM_API}/api/tasks" --data "$body"

      if [[ "$_HTTP_CODE" != "201" ]]; then
        die "提交失败（HTTP ${_HTTP_CODE}）：$(_api_error_detail "$_HTTP_BODY")"
      fi

      python3 - "$_HTTP_BODY" << 'PY'
import json, sys
d = json.loads(sys.argv[1])
conv = d.get("conversation_id", "")
print(f"\033[32m✓ 任务已提交：{d['id']}\033[0m")
print(f"  项目：{d['project'].split('/')[-1]}  账号：{d['account']} ({d['type']})")
if conv:
    print(f"  任务链：{conv}")
print(f"  查看日志：./aicm.sh task logs {d['id']}")
print(f"  实时跟踪：./aicm.sh task logs {d['id']} -f")
PY
      ;;

    # ── list：列出所有任务 ────────────────────────────────
    list)
      require_server
      local account_filter="${1:-}" status_filter="${2:-}"
      local url="${AICM_API}/api/tasks?limit=200"
      [[ -n "$account_filter" ]] && url="${url}&account=${account_filter}"
      [[ -n "$status_filter"  ]] && url="${url}&status=${status_filter}"

      _http_call GET "$url"
      [[ "$_HTTP_CODE" != "200" ]] && die "获取任务列表失败（HTTP ${_HTTP_CODE}）"

      python3 - "$_HTTP_BODY" << 'PY'
import json, sys
tasks = json.loads(sys.argv[1])
if not tasks:
    print("\033[33m⚠ 暂无任务记录\033[0m")
    sys.exit(0)
ST = {
    "running": ("\033[32m", "● 运行中"),
    "done":    ("\033[36m", "✓ 完成  "),
    "failed":  ("\033[31m", "✗ 失败  "),
    "killed":  ("\033[33m", "⊘ 已终止"),
}
RST = "\033[0m"
print()
print("  ── 任务列表 " + "─" * 62)
print(f"  \033[1m{'任务 ID':<22} {'状态':<8} {'账号':<16} {'类型':<8}  {'任务描述'}\033[0m")
print("  " + "─" * 78)
for t in tasks:
    color, label = ST.get(t["status"], ("", t["status"]))
    prompt = t["prompt"]
    if len(prompt) > 38:
        prompt = prompt[:38] + "…"
    print(f"  {t['id']:<22} {color}{label}{RST}  {t['account']:<16} {t['type']:<8}  {prompt}")
print()
PY
      ;;

    # ── status：查看单个任务详情 ──────────────────────────
    status)
      require_server
      local id="${1:-}"
      id="${id//$'\r'/}"
      [[ -z "$id" ]] && die "用法：./aicm.sh task status <任务ID>"

      _http_call GET "${AICM_API}/api/tasks/${id}"
      [[ "$_HTTP_CODE" != "200" ]] && \
        die "任务 '$id' 不存在或查询失败（HTTP ${_HTTP_CODE}）"

      python3 - "$_HTTP_BODY" << 'PY'
import json, sys
d = json.loads(sys.argv[1])
print()
print(f"  ID：      {d['id']}")
print(f"  状态：    {d['status']}")
print(f"  账号：    {d['account']} ({d['type']})")
print(f"  项目：    {d['project']}")
print(f"  创建：    {d['created']}")
print(f"  完成：    {d.get('finished') or '-'}")
if d.get("conversation_id"):
    print(f"  任务链：  {d['conversation_id']}")
if d.get("native_session_id"):
    print(f"  会话ID：  {d['native_session_id']}")
print(f"  任务描述：{d['prompt']}")
print()
PY
      ;;

    # ── logs：查看 / 实时跟踪日志 ─────────────────────────
    logs)
      require_server
      local id="${1:-}" follow=false
      [[ "${2:-}" == "-f" || "${2:-}" == "--follow" ]] && follow=true
      [[ -z "$id" ]] && die "用法：./aicm.sh task logs <任务ID> [-f]"
      id="${id//$'\r'/}"

      if $follow; then
        info "实时跟踪日志（Ctrl+C 退出）..."
        curl -sN "${AICM_API}/api/tasks/${id}/logs/stream?tail=80" \
          | while IFS= read -r line; do
              case "$line" in
                "data: [DONE]") break ;;
                "data: "*)      printf '%s\n' "${line#data: }" ;;
              esac
            done
      else
        _http_call GET "${AICM_API}/api/tasks/${id}/logs"
        [[ "$_HTTP_CODE" != "200" ]] && die "任务日志不存在：$id（HTTP ${_HTTP_CODE}）"
        printf '%s\n' "$_HTTP_BODY"
      fi
      ;;

    # ── kill：终止任务 ─────────────────────────────────────
    kill)
      require_server
      local id="${1:-}"
      id="${id//$'\r'/}"
      [[ -z "$id" ]] && die "用法：./aicm.sh task kill <任务ID>"

      _http_call DELETE "${AICM_API}/api/tasks/${id}"

      if [[ "$_HTTP_CODE" == "200" ]]; then
        success "已终止任务 ${id}"
      else
        die "终止失败（HTTP ${_HTTP_CODE}）：$(_api_error_detail "$_HTTP_BODY")"
      fi
      ;;

    # ── clean：清理旧任务记录 ──────────────────────────────
    clean)
      require_server
      local keep="${1:-30}"

      _http_call POST "${AICM_API}/api/tasks/clean?keep=${keep}"
      [[ "$_HTTP_CODE" != "200" ]] && die "清理失败（HTTP ${_HTTP_CODE}）"

      python3 -c "
import json, sys
d = json.loads(sys.argv[1])
print(f'已清理 {d[\"cleaned\"]} 条历史记录（保留 {d[\"kept\"]} 条）')
" "$_HTTP_BODY"
      ;;

    # ── help / 未知 ───────────────────────────────────────
    help|"")
      bold ""
      bold "  task 子命令"
      echo ""
      echo -e "  ${C_YELLOW}提交任务${C_RESET}"
      echo "    ./aicm.sh task run <描述> --project <项目名|路径>            一次性任务"
      echo "    ./aicm.sh task run <描述> --project <项目名|路径> --auto     全自动模式"
      echo "    ./aicm.sh task run <描述> --project <名> --new-chain <链名>  新建任务链"
      echo "    ./aicm.sh task run <描述> --conversation <ID>                续接任务链"
      echo "    ./aicm.sh task run <描述> --account <名称>                   指定账号"
      echo "    ./aicm.sh task run <描述> --type codex|claude --project <名> 指定类型"
      echo ""
      echo -e "  ${C_YELLOW}查看任务${C_RESET}"
      echo "    ./aicm.sh task list                    列出所有任务"
      echo "    ./aicm.sh task list <账号名>           按账号过滤"
      echo "    ./aicm.sh task status <任务ID>         查看任务详情"
      echo "    ./aicm.sh task logs <任务ID>           查看完整日志"
      echo "    ./aicm.sh task logs <任务ID> -f        实时跟踪日志"
      echo ""
      echo -e "  ${C_YELLOW}管理任务${C_RESET}"
      echo "    ./aicm.sh task kill <任务ID>           终止运行中的任务"
      echo "    ./aicm.sh task clean [N]               清理旧记录（默认保留30条）"
      echo ""
      echo -e "  ${C_YELLOW}前提${C_RESET}"
      echo "    task 命令通过 AICM 调度服务 API 执行，需先启动服务："
      echo "    ./aicm.sh server"
      echo "    也可通过环境变量指定远端服务：AICM_API=http://<host>:8765 ./aicm.sh task ..."
      echo ""
      echo -e "  ${C_YELLOW}示例${C_RESET}"
      echo "    ./aicm.sh task run \"实现用户登录功能\" --project my-app"
      echo "    ./aicm.sh task run \"修复所有 lint 错误\" --project ~/code/app --auto"
      echo "    ./aicm.sh task run \"新增支付模块\" --project my-app --new-chain 支付功能"
      echo "    ./aicm.sh task run \"继续上次任务\" --conversation conv-20240518-1234"
      echo "    ./aicm.sh task list"
      echo "    ./aicm.sh task logs 20240518123456-1234 -f"
      echo ""
      ;;

    *)
      error "未知 task 子命令：$sub"
      cmd_task help
      exit 1
      ;;
  esac
}

cmd_server() {
  load_config
  local port="${AICM_PORT:-8765}"
  local server_dir="$SCRIPT_DIR/server"

  [[ -f "$server_dir/main.py" ]] || die "找不到 server/main.py，请确认文件完整"

  if ! python3 -c "import fastapi" 2>/dev/null; then
    info "安装 Python 依赖..."
    pip3 install -r "$server_dir/requirements.txt" --break-system-packages -q
  fi

  info "启动 AICM 调度服务（端口 $port ）..."
  info "API 文档：http://localhost:${port}/docs"
  echo ""
  AICM_WORKSPACE="$SCRIPT_DIR" AICM_PORT="$port" \
    python3 "$server_dir/main.py"
}

# ============================================================
#  命令路由
# ============================================================

CMD="${1:-help}"
CMD="${CMD//$'\r'/}"
shift || true

case "$CMD" in
  build)          cmd_build ;;
  account)        cmd_account "$@" ;;
  project)        cmd_project "$@" ;;
  apply)          cmd_apply ;;
  up)             cmd_up ;;
  down)           cmd_down ;;
  restart)        cmd_restart ;;
  status)         cmd_status ;;
  logs)           cmd_logs "${1:-}" ;;
  enter)          cmd_enter "${1:-}" ;;
  login)          cmd_login "${1:-}" ;;
  check-proxy)    cmd_check_proxy ;;
  task)           cmd_task "$@" ;;
  server)         cmd_server ;;
  help|--help|-h) cmd_help ;;
  *)
    error "未知命令：$CMD"
    echo ""
    cmd_help
    exit 1
    ;;
esac
