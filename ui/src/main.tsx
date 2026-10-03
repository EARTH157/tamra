import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { initToken } from "./api";

initToken();
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
