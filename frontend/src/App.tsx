import { Navigate, Route, Routes } from "react-router-dom"

import { AppShell } from "@/components/layout/AppShell"
import { ChainPage } from "@/pages/ChainPage"
import { ContractPage } from "@/pages/ContractPage"
import { GroupsPage } from "@/pages/GroupsPage"
import { OverviewPage } from "@/pages/OverviewPage"
import { SystemPage } from "@/pages/SystemPage"

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<OverviewPage />} />
        <Route path="chains/:symbol" element={<ChainPage />} />
        <Route path="contracts/:occSymbol" element={<ContractPage />} />
        <Route path="groups" element={<GroupsPage />} />
        <Route path="system" element={<SystemPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
