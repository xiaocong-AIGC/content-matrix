import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 两种前端产物必须分开输出，否则会互相覆盖（踩过坑）：
//   dist      = 桌面版(Tauri)。API 走绝对地址 http://127.0.0.1:8010（见 .env.tauri），
//               因为 UI 从本地文件加载、没有同源后端。
//   dist-web  = 浏览器版。API 走相对路径，由 backend.exe 内嵌托管，同事通过
//               http://<本机IP>:8080 访问。
// 曾经两者都输出到 dist：打完桌面版再冻结 backend.exe，就把写死 127.0.0.1:8010 的
// 桌面版前端塞进了后端 → 同事在浏览器打开时去连自己电脑的 8010 → “后端连接失败”。
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  build: {
    outDir: mode === "tauri" ? "dist" : "dist-web",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8010",
    },
  },
}));
