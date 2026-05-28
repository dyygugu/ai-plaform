import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { RouterProvider } from "react-router-dom";

import { router } from "./routes/router";
import "./styles/global.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: "#2563eb", borderRadius: 10 } }}>
      <RouterProvider router={router} />
    </ConfigProvider>
  </React.StrictMode>,
);
