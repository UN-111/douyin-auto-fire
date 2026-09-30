import json
import random
import traceback
from pathlib import Path
from utils.logger import setup_logger
from utils.config import get_config, get_userData
from core.browser import get_browser
from core.douyin_im import (
    DEFAULT_STREAK_STICKER,
    STICKER_PANEL_WAIT_MS,
    DouyinIM,
    STATUS_READY,
    norm,
)


config = get_config()
userData = get_userData()
logger = setup_logger(level=config.get("logLevel", "Info"))
PROBE_ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "artifacts" / "sticker-probe"
PROBE_RESULT_FIELDS = (
    "composer_inventory",
    "ok",
    "sticker_name",
    "button_selector",
    "panel_selector",
    "sticker_item_selector",
    "sticker_match",
    "sticker_load_settle_ms",
    "sticker_image_loaded",
    "sticker_resource",
    "sticker_screenshot",
    "panel_wait_ms",
    "panel_wait_limit_ms",
    "screenshot",
    "screenshot_scope",
    "diagnostic_screenshot_pre_click",
    "diagnostic_screenshot_post_click",
    "trigger_attempts",
    "pre_click_panel_selector",
    "post_click_panel_selector",
    "sticker_clicked",
    "text_input",
    "message_sent",
    "chat_selected",
    "error",
)


def _probe_result(*, error=None, chat_selected=False):
    result = {
        "ok": False,
        "sticker_name": DEFAULT_STREAK_STICKER,
        "button_selector": None,
        "panel_selector": None,
        "sticker_item_selector": None,
        "sticker_match": None,
        "panel_wait_ms": None,
        "panel_wait_limit_ms": STICKER_PANEL_WAIT_MS,
        "screenshot": None,
        "screenshot_scope": None,
        "diagnostic_screenshot_pre_click": None,
        "diagnostic_screenshot_post_click": None,
        "trigger_attempts": [],
        "pre_click_panel_selector": None,
        "post_click_panel_selector": None,
        "sticker_clicked": False,
        "text_input": False,
        "message_sent": False,
        "chat_selected": bool(chat_selected),
    }
    if error:
        result["error"] = str(error)
    return result


def _write_probe_result(path, result):
    """Persist only the allowlisted, non-message probe fields."""
    safe = {key: result.get(key) for key in PROBE_RESULT_FIELDS if key in result}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def do_user_probe(
    browser,
    username,
    cookies,
    targets,
    *,
    artifact_dir=None,
    artifact_name="account",
):
    """Prove one native sticker without clicking it or touching the composer."""
    artifact_root = Path(artifact_dir) if artifact_dir else PROBE_ARTIFACT_DIR
    result_path = artifact_root / f"{artifact_name}.json"
    screenshot_path = artifact_root / f"{artifact_name}-panel.png"
    result = _probe_result()
    context = browser.new_context()
    context.set_default_navigation_timeout(config["browserActionTimeout"])
    context.set_default_timeout(config["browserActionTimeout"])
    page = context.new_page()
    context.add_cookies(cookies)

    im = None
    try:
        im = DouyinIM(
            page,
            timeout=config["imScanTimeout"],
            ready_timeout=config["imReadyTimeout"],
            settle_ms=config["friendListSettleMs"],
            max_steps=config["imMaxSteps"],
        )
        ready = im.wait_ready()
        if ready.get("status") != STATUS_READY:
            result["error"] = f"ready_{ready.get('status', 'unknown').lower()}"
        else:
            friend = next(iter(im.iter_find_and_select(targets[:1])), None)
            if friend is None:
                result["error"] = "target_chat_missing"
            else:
                result = im.probe_native_sticker(
                    friend,
                    DEFAULT_STREAK_STICKER,
                    screenshot_path=str(screenshot_path),
                )
                result["chat_selected"] = True

        _write_probe_result(result_path, result)
        if result.get("ok"):
            logger.info(
                f"账号 {username} 探针通过：原生表情面板已打开，"
                f"精确匹配耗时={result.get('panel_wait_ms')}ms"
            )
        else:
            logger.error(f"账号 {username} 探针未通过：{result.get('error', 'unknown')}")
        return bool(result.get("ok"))
    except Exception as exc:
        result = _probe_result(error=type(exc).__name__)
        try:
            _write_probe_result(result_path, result)
        except Exception:
            logger.warning("探针结果写入失败")
        logger.error(f"账号 {username} 探针异常：{type(exc).__name__}")
        return False
    finally:
        if im is not None:
            try:
                im.detach()
            except Exception:
                pass
        context.close()


def do_user_task(browser, username, cookies, targets):
    """一个账号的完整流程：门禁 → 滚动找人 → 发送 → 回执确认。

    实现委托给 `core.douyin_im.DouyinIM`：
      任务一（门禁）    DouyinIM 构造时自动完成，结论在 wait_ready() 里
      任务二（找人）    iter_find_and_select —— yield 时该会话已选中且 conv_id 已校验
      任务三（发送）    im.send_native_sticker —— 原生表情选择 + DOM 资源回执确认
    每次发送后按配置随机等待，避免以固定节奏继续扫描会话列表。
    """
    context = browser.new_context()  # 每个任务使用独立的上下文
    context.set_default_navigation_timeout(
        config["browserActionTimeout"]
    )  # 导航超时（毫秒，config 已换算好）
    context.set_default_timeout(
        config["browserActionTimeout"]
    )  # 单次操作默认超时（毫秒）

    page = context.new_page()

    # 注入 Cookie
    context.add_cookies(cookies)

    im = None
    try:
        # 打开抖音网页聊天页面由库内部完成（先挂钩子再导航，顺序不可颠倒）
        # 扫描参数全部来自配置：总预算/门禁等待是秒，静默窗是毫秒（见 utils.config）
        im = DouyinIM(
            page,
            timeout=config["imScanTimeout"],
            ready_timeout=config["imReadyTimeout"],
            settle_ms=config["friendListSettleMs"],
            max_steps=config["imMaxSteps"],
        )

        res = im.wait_ready()
        if res.get("status") != STATUS_READY:
            # 终端态都要显式打印，方便从日志一眼看出是哪种失败
            reason = {
                "LOGGED_OUT": "未登录（没有 sessionid）",
                "EXPIRED": "登录已失效（有 sessionid 但服务端不认）",
                "LOGIN_LOST": "运行期掉登录",
                "TIMEOUT": "等待超时",
                "ERROR": "内部错误",
            }.get(res.get("status"), res.get("status"))
            logger.error(f"账号 {username} 操作前检查未通过：{reason}，跳过该账号")
            return False

        logger.info(
            f"账号 {username} 门禁通过  user_id={res.get('user_id')} "
            f"nickname={res.get('nickname')} 会话列表就绪"
        )

        sent_ok = sent_fail = 0

        # 生成器：yield 出来的那一刻，对应好友的会话已经被选中
        for friend in im.iter_find_and_select(targets):
            logger.debug(f"账号 {username} 已选中好友 {friend['display']}，准备发送")
            sticker = DEFAULT_STREAK_STICKER
            r = im.send_native_sticker(friend, sticker)
            if r["ok"]:
                sent_ok += 1
                logger.info(
                    f"账号 {username} → {friend['display']} 发送成功"
                    f"（{r.get('via')} sticker={r.get('sticker')}）"
                )
            else:
                sent_fail += 1
                # A native picker click can dispatch before DOM confirmation.
                # Only retry when the sender explicitly proved it failed before
                # dispatch; an absent receipt must remain a single attempt.
                if r.get("retryable_before_dispatch"):
                    logger.warning(
                        f"账号 {username} → {friend['display']} 已确认发送前失败，重试一次"
                    )
                    try:
                        if friend.get("reselect") and friend["reselect"]():
                            r2 = im.send_native_sticker(friend, sticker)
                            if r2["ok"]:
                                sent_ok += 1
                                sent_fail -= 1
                                logger.info(
                                    f"账号 {username} → {friend['display']} 重试成功"
                                )
                    except Exception:
                        logger.warning(traceback.format_exc())
                else:
                    logger.warning(
                        f"账号 {username} → {friend['display']} 原生表情未确认，"
                        "不自动重试以避免重复发送"
                    )
            # 发送会把会话移到顶部；随机等待后再继续扫描列表。
            delay_ms = random.randint(
                config["recipientDelayMinMs"], config["recipientDelayMaxMs"]
            )
            page.wait_for_timeout(delay_ms)

        scan = im.last_scan or {}
        logger.info(
            f"账号 {username} 扫描结束：停止原因={scan.get('stopped')} "
            f"步数={scan.get('steps')} 访问会话={scan.get('visited')} "
            f"发送成功={sent_ok} 发送失败={sent_fail}"
        )
        if scan.get("missing"):
            # 这两句必须区分开：scanned_all=False 时"没找到"不代表"不存在"
            logger.warning(
                f"账号 {username} 未找到的目标：{scan['missing']}"
                f"（{scan.get('note')}）"
            )
        if scan.get("select_failed"):
            logger.warning(
                f"账号 {username} 找到但选中失败：{scan['select_failed']}"
            )

        folds = im.fold_groups()
        if any(v for v in folds.values() if v):
            logger.warning(
                f"账号 {username} 注意：折叠组/陌生人组里有内容 {folds}，"
                f"主列表扫不到，目标可能被折叠"
            )
        return bool(targets) and sent_fail == 0 and sent_ok == len(set(targets))

    finally:
        if im is not None:
            try:
                im.detach()
            except Exception:
                pass
        context.close()  # 任务完成后关闭上下文


def runTasks():
    # 检查是否启用多任务和任务数量
    # 创建信号量以限制并发任务数量
    logger.info("开始执行任务")
    logger.debug(f"当前配置如下：")
    logger.debug(f"消息模板: {config.get('messageTemplate', '未找到消息模板')}")
    logger.debug(f"一言类型: {config['hitokotoTypes']}")
    for user in userData:
        logger.debug(
            f"用户: {user.get('username', '未知用户')}, 目标好友: {user['targets']}"
        )

    if not userData:
        raise RuntimeError("没有可运行的账号配置")
    for user in userData:
        cookies = user["cookies"]
        # 归一化在**这里**做（配置读取端不做）：DouyinIM._match 内部用同一套 norm，
        # 两边都归过才谈得上相等 —— 否则配置里的「Ｌｕ瞳」永远匹配不上页面上的「Lu瞳」。
        # 同时丢掉归一后变空的项：空串留在剩余名单里永远扣不掉，会白滚到底。
        targets = [t for t in map(norm, user["targets"]) if t]
        username = user.get("username", "未知用户")
        fingerprint = user.get("fingerprint", None)
        logger.info(f"开始处理账号 {username}")
        # 创建任务
        browser = None
        try:
            browser = get_browser(fingerprint)
            if not do_user_task(browser, username, cookies, targets):
                raise RuntimeError("部分目标未确认发送成功，请检查运行日志")
            logger.info(f"账号 {username} 任务完成")
        finally:
            # 关闭浏览器实例
            if browser is not None:
                browser.close()


def runProbe():
    """Run the authenticated, no-send native sticker probe for each account."""
    logger.info("开始执行原生表情探针")
    results = []
    for index, user in enumerate(userData, start=1):
        cookies = user["cookies"]
        targets = [t for t in map(norm, user["targets"]) if t]
        username = user.get("username", "未知用户")
        fingerprint = user.get("fingerprint", None)
        if not targets:
            result_path = PROBE_ARTIFACT_DIR / f"account-{index:02d}.json"
            result = _probe_result(error="target_chat_missing")
            _write_probe_result(result_path, result)
            results.append(False)
            continue

        browser = None
        try:
            browser = get_browser(fingerprint)
            results.append(
                do_user_probe(
                    browser,
                    username,
                    cookies,
                    targets,
                    artifact_name=f"account-{index:02d}",
                )
            )
        finally:
            if browser is not None:
                browser.close()
    return bool(results) and all(results)
