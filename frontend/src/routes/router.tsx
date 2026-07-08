import { createBrowserRouter } from "react-router-dom";

import { AppLayout } from "../layouts/AppLayout";
import { AccountCoveragePage } from "../pages/AccountCoveragePage";
import { AccountsPage } from "../pages/AccountsPage";
import { AiPage } from "../pages/AiPage";
import { AbilityWorkbenchPage } from "../pages/AbilityWorkbenchPage";
import { AlertsPage } from "../pages/AlertsPage";
import { BackupsPage } from "../pages/BackupsPage";
import { DashboardPage } from "../pages/DashboardPage";
import { DataQualityPage } from "../pages/DataQualityPage";
import { EarningsPage } from "../pages/EarningsPage";
import { IncidentsPage } from "../pages/IncidentsPage";
import { OpsPage } from "../pages/OpsPage";
import { ObservabilityPage } from "../pages/ObservabilityPage";
import { ProductionPage } from "../pages/ProductionPage";
import { RulesPage } from "../pages/RulesPage";
import { SecurityPage } from "../pages/SecurityPage";
import { SettingsPage } from "../pages/SettingsPage";
import { TasksPage } from "../pages/TasksPage";
import { WorkersPage } from "../pages/WorkersPage";

export const router = createBrowserRouter([
  { path: "/", element: <AppLayout />, children: [
    { index: true, element: <DashboardPage /> },
    { path: "accounts", element: <AccountsPage /> },
    { path: "account-coverage", element: <AccountCoveragePage /> },
    { path: "ability-workbench", element: <AbilityWorkbenchPage /> },
    { path: "tasks", element: <TasksPage /> },
    { path: "earnings", element: <EarningsPage /> },
    { path: "data-quality", element: <DataQualityPage /> },
    { path: "ai", element: <AiPage /> },
    { path: "workers", element: <WorkersPage /> },
    { path: "rules", element: <RulesPage /> },
    { path: "backups", element: <BackupsPage /> },
    { path: "ops", element: <OpsPage /> },
    { path: "production", element: <ProductionPage /> },
    { path: "observability", element: <ObservabilityPage /> },
    { path: "alerts", element: <AlertsPage /> },
    { path: "incidents", element: <IncidentsPage /> },
    { path: "security", element: <SecurityPage /> },
    { path: "settings", element: <SettingsPage /> },
  ]},
]);







