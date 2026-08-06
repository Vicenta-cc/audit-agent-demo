import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { Plus } from "lucide-react";
import { Button } from "../../components/common/Button";
import {
  buildConfigCenterSnapshot,
  fetchConfigCenterSnapshot
} from "../../services/configCenter";
import type { ConfigCenterSnapshot } from "../../types/configCenter";
import { ConfigTabs } from "./ConfigTabs";
import { LexiconEditPage } from "./lexicon-edit/LexiconEditPage";
import { LexiconListPage } from "./LexiconListPage";
import { PolicyEditPage } from "./policy-edit/PolicyEditPage";
import { PolicyListPage } from "./PolicyListPage";

const emptySnapshot = buildConfigCenterSnapshot([], []);

export function ConfigCenterPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const isPolicyEditRoute = /\/config\/policies\/[^/]+\/edit$/.test(location.pathname);
  const isLexiconEditRoute = /\/config\/lexicons\/[^/]+\/edit$/.test(location.pathname);
  const activeTab = location.pathname.includes("/lexicons") ? "lexicons" : "policies";
  const [snapshot, setSnapshot] = useState<ConfigCenterSnapshot>(emptySnapshot);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const loadData = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    if (mode === "initial") {
      setLoading(true);
    } else {
      setRefreshing(true);
    }
    setError("");

    try {
      const next = await fetchConfigCenterSnapshot();
      setSnapshot(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "未知错误");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void loadData("initial");
  }, [loadData]);

  const handleCreate = () => {
    if (activeTab === "lexicons") {
      navigate("/config/lexicons/new/edit");
      return;
    }
    navigate("/config/policies/new/edit");
  };

  const handlePolicyDeleted = (policyId: string) => {
    setSnapshot((current) => {
      const policies = current.policies.filter((policy) => policy.id !== policyId);
      const lexicons = current.lexicons.map((lexicon) => ({
        ...lexicon,
        references: lexicon.references.filter((reference) => reference.id !== policyId)
      }));
      return buildConfigCenterSnapshot(policies, lexicons);
    });
  };

  const handleLexiconDeleted = (lexiconId: string) => {
    setSnapshot((current) => {
      const deleted = current.lexicons.find((lexicon) => lexicon.id === lexiconId);
      const policies = current.policies.map((policy) => ({
        ...policy,
        lexiconIds: policy.lexiconIds.filter((id) => id !== lexiconId),
        lexiconNames: policy.lexiconNames.filter((name) => name !== deleted?.name)
      }));
      const lexicons = current.lexicons.filter((lexicon) => lexicon.id !== lexiconId);
      return buildConfigCenterSnapshot(policies, lexicons);
    });
  };

  const createLabel = activeTab === "lexicons" ? "新建黑话库" : "新建方案";
  const routeProps = useMemo(
    () => ({
      loading,
      refreshing,
      error,
      onRefresh: () => loadData("refresh")
    }),
    [error, loadData, loading, refreshing]
  );

  if (isPolicyEditRoute || isLexiconEditRoute) {
    return (
      <Routes>
        <Route
          path="policies/:policyId/edit"
          element={
            <PolicyEditPage
              policies={snapshot.policies}
              lexicons={snapshot.lexicons}
              loading={loading}
              error={error}
              onRefresh={() => loadData("refresh")}
            />
          }
        />
        <Route
          path="lexicons/:lexiconId/edit"
          element={
            <LexiconEditPage
              lexicons={snapshot.lexicons}
              policies={snapshot.policies}
              loading={loading}
              error={error}
              onRefresh={() => loadData("refresh")}
            />
          }
        />
        <Route path="*" element={<Navigate to="policies" replace />} />
      </Routes>
    );
  }

  return (
    <main className="config-center-page">
      <header className="config-page-header">
        <div>
          <h1>配置底座</h1>
          <p>统一管理研判方案与风险知识库，为监控任务提供可复用的检测策略和知识资产。</p>
        </div>
        <Button type="button" variant="primary" onClick={handleCreate}>
          <Plus size={18} />
          {createLabel}
        </Button>
      </header>

      <div className="config-page-shell">
        <ConfigTabs />
        <Routes>
          <Route index element={<Navigate to="policies" replace />} />
          <Route
            path="policies"
            element={
              <PolicyListPage
                policies={snapshot.policies}
                summary={snapshot.policySummary}
                onPolicyDeleted={handlePolicyDeleted}
                {...routeProps}
              />
            }
          />
          <Route
            path="lexicons"
            element={
              <LexiconListPage
                lexicons={snapshot.lexicons}
                summary={snapshot.lexiconSummary}
                onLexiconDeleted={handleLexiconDeleted}
                {...routeProps}
              />
            }
          />
          <Route path="policies/:policyId/edit" element={<Navigate to="../policies" replace />} />
          <Route path="lexicons/:lexiconId/edit" element={<Navigate to="../lexicons" replace />} />
          <Route path="*" element={<Navigate to="policies" replace />} />
        </Routes>
      </div>
    </main>
  );
}
