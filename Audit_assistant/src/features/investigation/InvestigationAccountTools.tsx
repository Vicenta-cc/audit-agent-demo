import { TaskQuota } from "../../components/feedback/TaskQuota";
import { AccountMenu } from "../auth/AuthBoundary";

export function InvestigationAccountTools() {
  return <div className="inv-account-tools" aria-label="账号与任务额度"><TaskQuota compact /><AccountMenu compact /></div>;
}
