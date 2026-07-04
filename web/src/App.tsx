import { useEffect, useState } from "react";
import { getToken, UNAUTHORIZED_EVENT } from "./api/client";
import { Layout } from "./components/Layout";
import { toast, ToastRegion } from "./components/Toast";
import { TokenGate } from "./components/TokenGate";
import { Build } from "./screens/Build";
import { Integrations } from "./screens/Integrations";
import { Pipeline } from "./screens/Pipeline";
import { Resources } from "./screens/Resources";
import { Settings } from "./screens/Settings";
import { Today } from "./screens/Today";
import { Triage } from "./screens/Triage";

export type Route =
  | "today"
  | "resources"
  | "triage"
  | "pipeline"
  | "integrations"
  | "settings"
  | "build";

function parseRoute(): Route {
  // Split off any ?query — the Resources screen keeps its filter state in the
  // hash query (#/resources?category=…), which must not defeat route matching.
  const hash = window.location.hash.replace(/^#\/?/, "").split("?")[0];
  if (hash === "resources" || hash === "triage" || hash === "pipeline" ||
      hash === "integrations" || hash === "settings" || hash === "build")
    return hash;
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
        {route === "resources" && <Resources />}
        {route === "triage" && <Triage />}
        {route === "pipeline" && <Pipeline />}
        {route === "integrations" && <Integrations />}
        {route === "settings" && <Settings />}
        {route === "build" && <Build />}
      </Layout>
      <ToastRegion />
    </>
  );
}
