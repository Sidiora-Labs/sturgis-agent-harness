import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import StandaloneGateway from "./pages/gateway/StandaloneGateway";
import "./global.css";
import { configureApiSession } from "./lib/api";
configureApiSession("gateway");
const client = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false },
    mutations: { retry: false },
  },
});
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <StandaloneGateway />
    </QueryClientProvider>
  </StrictMode>,
);
