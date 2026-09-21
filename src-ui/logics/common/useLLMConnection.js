import { useStdoutToPython } from "@useStdoutToPython";
import {
    useStore_IsLMStudioConnected,
    useStore_IsOllamaConnected,
    useStore_CodexStatus,
    useStore_IsCodexInstalling,
    useStore_IsCodexLoggingIn,
} from "@store";

export const useLLMConnection = () => {
    const { asyncStdoutToPython } = useStdoutToPython();
    const {
        currentIsLMStudioConnected,
        updateIsLMStudioConnected,
        pendingIsLMStudioConnected,
    } = useStore_IsLMStudioConnected();
    const {
        currentIsOllamaConnected,
        updateIsOllamaConnected,
        pendingIsOllamaConnected,
    } = useStore_IsOllamaConnected();
    const {
        currentCodexStatus,
        updateCodexStatus,
        pendingCodexStatus,
    } = useStore_CodexStatus();
    const {
        currentIsCodexInstalling,
        updateIsCodexInstalling,
    } = useStore_IsCodexInstalling();
    const {
        currentIsCodexLoggingIn,
        updateIsCodexLoggingIn,
    } = useStore_IsCodexLoggingIn();

    const checkConnection_LMStudio = () => {
        pendingIsLMStudioConnected();
        asyncStdoutToPython("/run/lmstudio_connection");
    };
    const setConnectionStatus_LMStudio = (is_connected) => {
        updateIsLMStudioConnected(is_connected);
    };

    const checkConnection_Ollama = () => {
        pendingIsOllamaConnected();
        asyncStdoutToPython("/run/ollama_connection");
    };
    const setConnectionStatus_Ollama = (is_connected) => {
        updateIsOllamaConnected(is_connected);
    };

    // Codex / ChatGPT
    //
    // install と login は完了までに数分かかりうるので、バックエンドは即座に
    // 200 を返し、結果を /run/codex_install / /run/codex_login で push して
    // くる (項目44)。ここでは押した瞬間に "実行中" フラグを立て、push が
    // 返ってきたら下ろす。
    const checkConnection_Codex = () => {
        pendingCodexStatus();
        asyncStdoutToPython("/run/codex_connection");
    };
    const fetchStatus_Codex = () => {
        asyncStdoutToPython("/get/data/codex_status");
    };
    const setStatus_Codex = (status) => {
        updateCodexStatus(status);
    };
    const installCodexCLI = () => {
        updateIsCodexInstalling(true);
        asyncStdoutToPython("/run/codex_install");
    };
    const finishInstall_Codex = (status) => {
        // このルートには2種類が来る:
        //   1. 押した直後のエンドポイント応答 (payload は true) = "開始した"
        //   2. 別スレッドの処理完了 push (payload は status オブジェクト)
        // 1 で実行中フラグを下ろすと、始まった瞬間にスピナーが消える。
        if (status === true) return;

        updateIsCodexInstalling(false);
        // 失敗時のペイロードは status 形ではないので、その場合は状態を
        // 取り直す。成功時はそのまま反映して余計な往復を省く。
        if (status && typeof status.installed === "boolean") {
            updateCodexStatus(status);
        } else {
            fetchStatus_Codex();
        }
    };
    const connectChatGPT_Codex = () => {
        updateIsCodexLoggingIn(true);
        asyncStdoutToPython("/run/codex_login");
    };
    const finishLogin_Codex = (status) => {
        // finishInstall_Codex と同じ理由で、開始応答 (true) は無視する。
        if (status === true) return;

        updateIsCodexLoggingIn(false);
        if (status && typeof status.installed === "boolean") {
            updateCodexStatus(status);
        } else {
            fetchStatus_Codex();
        }
    };
    const disconnectChatGPT_Codex = () => {
        asyncStdoutToPython("/run/codex_logout");
    };

    return {
        currentIsLMStudioConnected,
        updateIsLMStudioConnected,
        setConnectionStatus_LMStudio,
        checkConnection_LMStudio,

        currentIsOllamaConnected,
        updateIsOllamaConnected,
        setConnectionStatus_Ollama,
        checkConnection_Ollama,

        currentCodexStatus,
        updateCodexStatus,
        setStatus_Codex,
        fetchStatus_Codex,
        checkConnection_Codex,

        currentIsCodexInstalling,
        updateIsCodexInstalling,
        installCodexCLI,
        finishInstall_Codex,

        currentIsCodexLoggingIn,
        updateIsCodexLoggingIn,
        connectChatGPT_Codex,
        finishLogin_Codex,
        disconnectChatGPT_Codex,
    };
};