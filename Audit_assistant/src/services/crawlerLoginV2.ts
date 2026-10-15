import { API_BASE, apiRequest } from "./apiClient";
import { loginControlHeaders } from "./crawlerAccounts";
export interface ChallengeArea {
  challenge_id: string;
  area_version: number;
  kind: string;
  interaction_mode: string;
  clip_rect: { x: number; y: number; width: number; height: number };
  viewport: { width: number; height: number };
  image_width: number;
  image_height: number;
  allowed_actions: string[];
}
export interface LoginSnapshot {
  protocol_version: 2;
  engine: string;
  id: string;
  operation_id: string;
  account_id: string;
  status: string;
  phase: string;
  state_revision: number;
  controller_epoch: number;
  controller_state: string;
  available_actions: string[];
  expires_at: string;
  qr: null | {
    revision: number | string;
    state: string;
    image_data_url: string;
    expires_at: string | null;
  };
  sms: null | {
    fields?: Array<
      string | { name: string; label?: string; max_length?: number }
    >;
    masked_phone?: string;
    resend_available_at?: string;
    send_status?: string;
    error?: string;
  };
  challenge: ChallengeArea | null;
  identity?: { status: string; platform_account_id?: string };
  result?: {
    auth_saved: boolean;
    identity_verified: boolean;
    reuse_verified: boolean;
    account_eligibility: string;
    platform_account_id?: string;
  } | null;
  error?: string | { message?: string; code?: string } | null;
}
export interface LoginEditableTarget {
  field_id: string;
  rect: { x: number; y: number; width: number; height: number };
  input_type: string;
  input_mode: string;
  max_length: number;
  value: string;
  focused: boolean;
  applied_edit_revision: number;
  selection_start?: number | null;
  selection_end?: number | null;
  selection_direction?: "none" | "forward" | "backward" | null;
}
export interface LoginCommandReceipt {
  command_id: string;
  sequence: number;
}
export interface LoginCommandResult {
  type?: string;
  command_id: string;
  execution_status?: string;
  focused_field_id?: string;
  field_id?: string;
  client_edit_revision?: number;
  error_message?: string;
  message?: string;
}
export interface LoginFrame {
  sequence: number;
  area_version: number;
  metadata: {
    controller_epoch: number;
    challenge_id?: string;
    image_width?: number;
    image_height?: number;
    codec?: string;
    applied_input_sequence?: number;
    editable_targets?: LoginEditableTarget[];
  };
  image: Blob;
}
export function decodeLoginFrame(buffer: ArrayBuffer): LoginFrame {
  if (buffer.byteLength < 28) throw new Error("验证画面数据不完整");
  const view = new DataView(buffer);
  const bytes = new Uint8Array(buffer);
  if (
    String.fromCharCode(...bytes.subarray(0, 4)) !== "DLV2" ||
    view.getUint8(4) !== 2 ||
    view.getUint8(5) !== 1
  )
    throw new Error("验证画面格式不支持");
  const metaLength = view.getUint16(6);
  const imageLength = view.getUint32(8);
  if (
    metaLength > 8192 ||
    imageLength > 1048576 ||
    buffer.byteLength !== 28 + metaLength + imageLength
  )
    throw new Error("验证画面长度无效");
  const metadata = JSON.parse(
    new TextDecoder().decode(bytes.subarray(28, 28 + metaLength)),
  );
  return {
    sequence: view.getUint32(12),
    area_version: view.getUint32(16),
    metadata,
    image: new Blob([buffer.slice(28 + metaLength)], { type: "image/jpeg" }),
  };
}
export async function createLoginSocket(
  id: string,
): Promise<{ socket: WebSocket; ticket: string }> {
  const { ticket } = await apiRequest<{ ticket: string }>(
    `/api/crawler-account-login-sessions/${encodeURIComponent(id)}/ws-tickets`,
    { method: "POST", headers: loginControlHeaders() },
  );
  const url = new URL(
    `${API_BASE()}/api/crawler-account-login-sessions/${encodeURIComponent(id)}/stream`,
  );
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(url);
  socket.binaryType = "arraybuffer";
  return { socket, ticket };
}
