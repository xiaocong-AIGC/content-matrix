import { useState } from "react";
import { Check, Copy, KeyRound, Server, Sparkles } from "lucide-react";

import {
  finishBootstrap,
  saveAiConfig,
  setAdbEndpoints,
  setAdminToken,
} from "../lib/api";

/**
 * 首次启动向导 —— 全新/换电脑的空系统才出现。三步:
 *   1) 显示本机的管理员令牌(一次性,让用户保存)并自动登录本机;
 *   2) 填云机 adb 地址;
 *   3) 填 AI Key(可跳过)。
 * 做完 POST /auth/bootstrap-done, 之后不再出现。
 */
export function FirstRunWizard({
  masterToken,
  adbDefault,
  onDone,
}: {
  masterToken: string;
  adbDefault: string;
  onDone: () => void;
}) {
  const [step, setStep] = useState(1);
  const [copied, setCopied] = useState(false);
  const [adb, setAdb] = useState(adbDefault || "192.168.1.200:6001-6020");
  const [aiUrl, setAiUrl] = useState("https://api.deepseek.com");
  const [aiModel, setAiModel] = useState("deepseek-chat");
  const [aiKey, setAiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  // Step 1 already has the token — log THIS browser in with it immediately so the
  // subsequent admin calls (adb endpoints / AI key) are authorized.
  function useTokenAndNext() {
    setAdminToken(masterToken);
    setStep(2);
  }

  async function saveAdbNext() {
    setBusy(true);
    setErr("");
    try {
      await setAdbEndpoints(adb.trim());
      setStep(3);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function finish(skipAi: boolean) {
    setBusy(true);
    setErr("");
    try {
      if (!skipAi && aiKey.trim()) {
        await saveAiConfig({
          api_key: aiKey.trim(),
          base_url: aiUrl.trim(),
          model: aiModel.trim(),
        });
      }
      await finishBootstrap();
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "设置没能完成，请重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop wizard-backdrop" role="presentation">
      <section className="wizard" role="dialog" aria-modal="true">
        <header className="wizard-head">
          <span>图文矩阵 · 首次配置</span>
          <div className="wizard-steps">
            {[1, 2, 3].map((n) => (
              <i key={n} className={n <= step ? "on" : ""} />
            ))}
          </div>
        </header>

        {step === 1 && (
          <div className="wizard-body">
            <KeyRound size={30} strokeWidth={1.2} />
            <h2>这是你的管理员令牌</h2>
            <p>
              请<b>妥善保存</b>。以后登录控制台都用它，换别的电脑或手机也一样。
              这个令牌只显示这一次。
            </p>
            <div className="wizard-token">
              <code>{masterToken}</code>
              <button
                className="button secondary"
                onClick={() => {
                  navigator.clipboard?.writeText(masterToken).then(
                    () => {
                      setCopied(true);
                      setTimeout(() => setCopied(false), 1500);
                    },
                    () => {},
                  );
                }}
              >
                {copied ? <Check size={15} /> : <Copy size={15} />}
                {copied ? "已复制" : "复制"}
              </button>
            </div>
            <div className="wizard-actions">
              <button className="button primary" onClick={useTokenAndNext}>
                我已保存，下一步
              </button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="wizard-body">
            <Server size={30} strokeWidth={1.2} />
            <h2>云手机地址</h2>
            <p>
              云手机的 adb 地址，多个用逗号分隔，支持端口范围。不确定就先用默认值，以后可在设置里改。
            </p>
            <input
              className="wizard-input"
              value={adb}
              onChange={(e) => setAdb(e.target.value)}
              placeholder="192.168.1.200:6001-6020"
            />
            {err && <p className="form-error">{err}</p>}
            <div className="wizard-actions">
              <button className="button secondary" onClick={() => setStep(1)}>
                上一步
              </button>
              <button className="button primary" disabled={busy} onClick={saveAdbNext}>
                下一步
              </button>
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="wizard-body">
            <Sparkles size={30} strokeWidth={1.2} />
            <h2>AI 密钥（可跳过）</h2>
            <p>填了才能用 AI 生成文案。接口地址填 OpenAI 兼容的网关。以后也能在 AI 创作页改。</p>
            <input
              className="wizard-input"
              value={aiUrl}
              onChange={(e) => setAiUrl(e.target.value)}
              placeholder="https://api.deepseek.com"
            />
            <input
              className="wizard-input"
              value={aiModel}
              onChange={(e) => setAiModel(e.target.value)}
              placeholder="deepseek-chat"
            />
            <input
              className="wizard-input"
              type="password"
              value={aiKey}
              onChange={(e) => setAiKey(e.target.value)}
              placeholder="粘贴 API Key（可留空跳过）"
            />
            {err && <p className="form-error">{err}</p>}
            <div className="wizard-actions">
              <button className="button secondary" disabled={busy} onClick={() => finish(true)}>
                跳过，直接进入
              </button>
              <button className="button primary" disabled={busy} onClick={() => finish(false)}>
                完成
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
