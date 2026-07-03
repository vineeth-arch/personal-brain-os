import { useEffect, useState } from "react";
import { getToken, UNAUTHORIZED_EVENT } from "./api/client";
import { Layout } from "./components/Layout";
import { toast, ToastRegion } from "./components/Toast";
import { TokenGate } from "./components/TokenGate";
import { Pipeline } from "./screens/Pipeline";
import { Settings } from "./screens/Settings";
import { Today } from "./screens/Today";
import { Triage } from "./screens/Triage";

export type Route = "today" | "triage" | "pipeline" | "settings";

function parseRoute(): Route {
  const hash = window.location.hash.replace(/^#\/?/, "");
  if (hash === "triage" || hash === "pipeline" || hash === "settings") return hash;
  return "today";
}

function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(parseRoute);
  useEffect(() => {
    const onChange = () => setRoute(parseRoute());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export default function App() {
  const route = useHashRoute();
  const [connected, setConnected] = useState(() => Boolean(getToken()));

  useEffect(() => {
    const onUnauthorized = () => {
      setConnected(false);
      toast("The server rejected the token — reconnect to continue.", "error");
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  if (!connected) {
    return (
      <>
        <TokenGate onConnected={() => setConnected(true)} />
        <ToastRegion />
      </>
    );
  }

  return (
    <>
      <Layout route={route}>
        {route === "today" && <Today />}
        {route === "triage" && <Triage />}
        {route === "pipeline" && <Pipeline />}
        {route === "settings" && <Settings />}
      </Layout>
      <ToastRegion />
    </>
  );
}
