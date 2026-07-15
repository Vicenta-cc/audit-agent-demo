import { focusUserAccounts } from "../mocks/focusUsers";
import type { MonitoredAccount } from "../types/focusUsers";

export async function fetchMonitoredAccounts(): Promise<MonitoredAccount[]> {
  await new Promise((resolve) => window.setTimeout(resolve, 120));
  return JSON.parse(JSON.stringify(focusUserAccounts)) as MonitoredAccount[];
}
