import { useState } from "react";
import styles from "./CodexConnection.module.scss";
import { useI18n } from "@useI18n";

/**
 * Codex / ChatGPT の状態表示 + 主アクション1つ (項目5, 39)。
 *
 * LMStudio/Ollama の ConnectionCheckButton と違って真偽値1つでは描けない。
 * 出し分けるのは4状態:
 *
 *   D. 未インストール          -> [Install Codex CLI]
 *   C. インストール済み/未ログイン -> [Connect ChatGPT]
 *   B. APIキーでログイン済み     -> [Connect ChatGPT] + 警告
 *   A. ChatGPT でログイン済み    -> [Reconnect] / [Disconnect]
 *
 * ボタンは常に1つだけ主役にする。普通のユーザーに Node.js / npm / winget /
 * PATH を説明しないという方針 (項目5 状態D) なので、「Node.js が要る」
 * といった内部事情は一切出さず、必要な依存はインストール処理が自分で面倒を
 * 見る。
 */
export const CodexConnection = (props) => {
    const { t } = useI18n();
    const {
        status,
        is_installing,
        is_logging_in,
        installFunction,
        connectFunction,
        disconnectFunction,
    } = props;

    // 勝手にソフトウェアを入れないための同意ステップ (項目7)。
    // モーダルの仕組みが既存に無いので、ボタンをその場で確認UIに
    // 差し替える2段階方式にしている。
    const [is_confirming_install, setIsConfirmingInstall] = useState(false);

    const is_busy = is_installing || is_logging_in;
    const is_installed = status?.installed === true;
    const is_connected = status?.connected === true;
    const is_api_key_auth = status?.auth_mode === "api_key";

    const cli_state_label = is_installed
        ? `${t("config_page.translation.codex.installed")} ✅`
        : `${t("config_page.translation.codex.not_installed")} ❌`;

    const account_state_label = is_connected
        ? `${t("config_page.translation.codex.connected")} ✅`
        : is_api_key_auth
            ? `${t("config_page.translation.codex.api_key_auth")} ⚠️`
            : `${t("config_page.translation.codex.not_connected")} ❌`;

    const busy_label = is_installing
        ? `${t("config_page.translation.codex.installing")} 🌀`
        : `${t("config_page.translation.codex.connecting")} 🌀`;

    const onClickInstall = () => {
        setIsConfirmingInstall(false);
        installFunction?.();
    };

    return (
        <div className={styles.container}>
            <div className={styles.status_rows}>
                <p className={styles.status_label}>
                    {t("config_page.translation.codex.cli_status")}: {cli_state_label}
                </p>
                {/* 未インストールのときにアカウント状態を出しても意味がないので隠す。 */}
                {is_installed && (
                    <p className={styles.status_label}>
                        {t("config_page.translation.codex.account_status")}: {account_state_label}
                    </p>
                )}
                {status?.version && (
                    <p className={styles.version_label}>{status.version}</p>
                )}
            </div>

            {is_busy ? (
                /* 信頼できる進捗率が取れないので割合は出さない (項目15)。 */
                <p className={styles.busy_label}>{busy_label}</p>
            ) : is_confirming_install ? (
                <div className={styles.confirm_container}>
                    <p className={styles.confirm_text}>
                        {t("config_page.translation.codex.install_confirm")}
                    </p>
                    <div className={styles.confirm_buttons}>
                        <button
                            className={styles.button_wrapper}
                            onClick={() => setIsConfirmingInstall(false)}
                        >
                            <p className={styles.button_label}>
                                {t("config_page.translation.codex.cancel")}
                            </p>
                        </button>
                        <button className={styles.button_wrapper} onClick={onClickInstall}>
                            <p className={styles.button_label}>
                                {t("config_page.translation.codex.install")}
                            </p>
                        </button>
                    </div>
                </div>
            ) : !is_installed ? (
                <button
                    className={styles.button_wrapper}
                    onClick={() => setIsConfirmingInstall(true)}
                >
                    <p className={styles.button_label}>
                        {t("config_page.translation.codex.install_cli")}
                    </p>
                </button>
            ) : (
                <div className={styles.confirm_buttons}>
                    <button className={styles.button_wrapper} onClick={connectFunction}>
                        <p className={styles.button_label}>
                            {is_connected
                                ? t("config_page.translation.codex.reconnect")
                                : t("config_page.translation.codex.connect_chatgpt")}
                        </p>
                    </button>
                    {is_connected && (
                        <button className={styles.button_wrapper} onClick={disconnectFunction}>
                            <p className={styles.button_label}>
                                {t("config_page.translation.codex.disconnect")}
                            </p>
                        </button>
                    )}
                </div>
            )}
        </div>
    );
};
