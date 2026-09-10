import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import {
  ArrowLeft,
  AtSign,
  Bot,
  BookOpenText,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  FileCheck2,
  FileText,
  FolderSearch,
  GitCompareArrows,
  ListTree,
  LoaderCircle,
  Menu,
  MoreHorizontal,
  Paperclip,
  Pencil,
  Plus,
  SearchCheck,
  Send,
  ShieldCheck,
  SquarePen,
  Sparkles,
  Tags,
  Trash2,
  X
} from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";
import { useRuleAssistantWorkspace } from "./RuleAssistantWorkspaceContext";
import type { RiskRule } from "../../types/investigation";
import {
  addedTransparentClothingRule,
  createTerrorLexiconCandidate,
  gamblingLexiconSuggestedTerms,
  mockRuleFileName,
  mockTerrorLexiconFileName,
  mockTerrorRuleFileName,
  modifiedLocalCloseupContent,
  originalLocalCloseupRule
} from "./mockData";
import type {
  RuleAssistantConversation,
  RuleAssistantDrawerState,
  RuleAssistantLexicon,
  RuleAssistantMessage,
  RuleAssistantMessageResourceContext,
  RuleAssistantRuleSet,
  RuleAssistantRouteState
} from "./types";

const addRulePrompt = "在“身体隐私部位暴露”中增加一条“透明衣物透视展示”规则。";
const modifyRulePrompt = "把“局部露拍”的风险等级调整为高风险，并补充持续聚焦、反复展示的判断条件。";
const resourceInventoryPrompt = "系统里有哪些与私域引流相关的规则和召回词？分别在哪些审核规则和词库里？";
const resourceCoveragePrompt = "如果我要专门做“二维码和联系方式引流”这一类内容，现有资源够用吗？";
const categoryCountPrompt = "请问该审核规则下有几个风险类别？";
const highRiskPrompt = "高风险的规则有哪些？";
const importRuleFilePrompt = "请解析这份规则文件，并整理成完整规则结构。";

type WorkspaceResourceSelection = {
  type: "rule-set" | "lexicon";
  id: string;
};

type MockAttachment = {
  name: string;
  fileType: "DOCX" | "XLSX";
  description: string;
};

const mockAttachments: MockAttachment[] = [
  { name: mockRuleFileName, fileType: "DOCX", description: "导入或更新现有审核规则" },
  { name: mockTerrorRuleFileName, fileType: "DOCX", description: "创建暴恐审核规则" },
  { name: mockTerrorLexiconFileName, fileType: "XLSX", description: "创建暴恐涉敏黑话库" }
];

function getConversationRecency(updatedAt: string, fallback: number) {
  const now = new Date();
  if (updatedAt === "刚刚") return now.getTime() + 1;
  if (updatedAt === "昨天") {
    return new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1, 23, 59).getTime();
  }
  if (/^\d{1,2}:\d{2}$/.test(updatedAt)) {
    const [hours, minutes] = updatedAt.split(":").map(Number);
    return new Date(now.getFullYear(), now.getMonth(), now.getDate(), hours, minutes).getTime();
  }
  if (/^\d{2}-\d{2}$/.test(updatedAt)) {
    const [month, day] = updatedAt.split("-").map(Number);
    return new Date(now.getFullYear(), month - 1, day, 12).getTime();
  }
  const parsed = Date.parse(updatedAt);
  return Number.isNaN(parsed) ? fallback : parsed;
}

function inferResourceCreation(text: string): "rule-set" | "lexicon" | null {
  const normalized = text.replace(/\s/g, "");
  const requestsCreation = /(新建|创建|建立)/.test(normalized);
  if (!requestsCreation) return null;
  if (/(黑话库|词库)/.test(normalized)) return "lexicon";
  if (/(审核规则|审核规则)/.test(normalized)) return "rule-set";
  return null;
}

function inferExistingResource(
  text: string,
  ruleSets: RuleAssistantRuleSet[],
  lexicons: RuleAssistantLexicon[]
): WorkspaceResourceSelection | null {
  const normalized = text.replace(/\s/g, "");
  const matchedRuleSet = ruleSets.find((ruleSet) => normalized.includes(ruleSet.name.replace(/\s/g, "")));
  if (matchedRuleSet) return { type: "rule-set", id: matchedRuleSet.id };
  const matchedLexicon = lexicons.find((lexicon) => normalized.includes(lexicon.name.replace(/\s/g, "")));
  if (matchedLexicon) return { type: "lexicon", id: matchedLexicon.id };
  if (/(局部露拍|局部怼拍|色情低俗风险)/.test(normalized)) return { type: "rule-set", id: "ruleset-erotic" };
  if (/(涉赌博彩黑话库|世界杯博彩召回词)/.test(normalized)) return { type: "lexicon", id: "recall-gambling" };
  return null;
}

function getSuggestedPrompt(conversation: RuleAssistantConversation) {
  const activeMessages = conversation.messages.filter((message) => message.status !== "cancelled");
  const hasImportedFile = activeMessages.some((message) => message.kind === "file-analysis");
  const hasAddedRule = activeMessages.some((message) => message.kind === "new-rule-suggestion");
  const hasModifiedRule = activeMessages.some((message) => message.kind === "rule-change-suggestion");
  const lexiconSuggestion = [...activeMessages]
    .reverse()
    .find((message) => message.kind === "lexicon-suggestion");

  if (conversation.scenario === "create-terror-rule-set" && hasImportedFile) return null;
  if (conversation.scenario === "create-terror-lexicon" && activeMessages.some((message) => message.kind === "lexicon-file-analysis")) return null;

  if (hasImportedFile) {
    if (!hasAddedRule) return addRulePrompt;
    if (!hasModifiedRule) return modifyRulePrompt;
    return null;
  }

  const latestAssistantMessage = [...activeMessages].reverse().find((message) => message.role === "assistant");
  if (hasModifiedRule && !hasAddedRule) return addRulePrompt;
  if (hasAddedRule && !hasModifiedRule) return modifyRulePrompt;
  if (latestAssistantMessage?.kind === "resource-inventory") return resourceCoveragePrompt;
  if (latestAssistantMessage?.kind === "resource-coverage") return null;
  if (latestAssistantMessage?.kind === "ruleset-summary") return highRiskPrompt;
  if (latestAssistantMessage?.kind === "high-risk-rules") return null;

  if (conversation.messages.length === 0) {
    if (conversation.purpose === "create-rule-set") return "请先帮我梳理这份审核规则需要覆盖的风险分类。";
    if (conversation.purpose === "create-lexicon") return "请先帮我梳理这份词库需要覆盖的召回主题。";
    if (conversation.lexiconId) return "当前有哪些世界杯博彩相关召回词？";
    if (conversation.ruleSetId === null) return resourceInventoryPrompt;
    if (conversation.scenario === "import-edit") return importRuleFilePrompt;
    if (conversation.scenario === "direct-edit") return addRulePrompt;
    return categoryCountPrompt;
  }

  if (conversation.lexiconId === "recall-gambling") {
    if (lexiconSuggestion?.status === "completed") {
      return latestAssistantMessage?.kind === "lexicon-answer"
        ? null
        : "查看当前世界杯博彩相关召回词。";
    }
    return lexiconSuggestion ? null : "补充世界杯博彩相关召回词。";
  }
  return null;
}

function createId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

function currentTime() {
  return new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function buildMockReply(text: string, conversation: RuleAssistantConversation): RuleAssistantMessage {
  const normalized = text.replace(/\s/g, "");
  const isLexicon = Boolean(conversation.lexiconId);
  const isGamblingLexicon = conversation.lexiconId === "recall-gambling";
  const isGlobal = conversation.ruleSetId === null && !isLexicon;
  const existingLexiconSuggestion = [...conversation.messages]
    .reverse()
    .find((message) => message.kind === "lexicon-suggestion" && message.status !== "cancelled");

  if (isGlobal && /私域引流/.test(normalized) && /(规则|召回词|词库)/.test(normalized)) {
    return {
      id: createId("resource-a"),
      role: "assistant",
      kind: "resource-inventory",
      content: "目前找到 1 个相关审核规则和 2 个相关黑话库。",
      sourceRuleSetIds: ["ruleset-gambling"],
      sourceLexiconIds: ["recall-gambling", "recall-fraud"]
    };
  }

  if (isGlobal && /(二维码|联系方式)/.test(normalized) && /(够用|足够|专项|专门)/.test(normalized)) {
    return {
      id: createId("resource-a"),
      role: "assistant",
      kind: "resource-coverage",
      content: "现有资源只能覆盖部分场景，还不足以独立支撑这一专项。",
      sourceRuleSetIds: ["ruleset-gambling"],
      sourceLexiconIds: ["recall-gambling", "recall-fraud"]
    };
  }

  if (!isGlobal && !isLexicon && /(几个|多少).*(风险类别|风险分类)|(风险类别|风险分类).*(几个|多少)/.test(normalized)) {
    return {
      id: createId("rule-a"),
      role: "assistant",
      kind: "ruleset-summary",
      content: "当前“色情低俗审核规则”包含 1 个风险类别，即“身体隐私部位暴露”，共 2 条风险规则：“明确暴露”和“局部露拍”。该类别主要用于识别直接暴露敏感隐私部位，或通过镜头聚焦、局部特写等方式突出敏感部位的内容。",
      sourceRuleSetIds: [conversation.ruleSetId || "ruleset-erotic"]
    };
  }

  // A modification request can also contain the generic query words below, so keep
  // the existing mutation route ahead of read-only rule lookup routes.
  if (/(修改|调整|改成).*(局部露拍|局部怼拍)|(局部露拍|局部怼拍).*(风险等级|高风险|3秒|修改|调整)/.test(normalized)) {
    return {
      id: createId("rule-a"),
      role: "assistant",
      kind: "rule-change-suggestion",
      content: "我已整理出具体修改前后差异，确认前不会更新任何正式规则。",
      sourceRuleSetIds: [conversation.ruleSetId || "ruleset-erotic"]
    };
  }

  if (!isGlobal && !isLexicon && /高风险/.test(normalized) && /(哪些|规则|多少|列出)/.test(normalized)) {
    return {
      id: createId("rule-a"),
      role: "assistant",
      kind: "high-risk-rules",
      content: "当前共有 1 条高风险规则：“明确暴露”。该规则主要针对直接、清晰展示生殖器、女性乳头及乳晕等敏感隐私部位的内容；新闻报道、案件通报或合理医疗科普等具有明确合法语境的内容可以豁免。",
      sourceRuleSetIds: [conversation.ruleSetId || "ruleset-erotic"]
    };
  }

  if (isGamblingLexicon && /(新增|增加|添加|补充).*(世界杯|欧洲杯|赛事|博彩|滚球)|(世界杯|欧洲杯|赛事|博彩|滚球).*(新增|增加|添加|补充)/.test(normalized)) {
    if (existingLexiconSuggestion) {
      return {
        id: createId("lexicon-a"),
        role: "assistant",
        kind: "lexicon-answer",
        content: existingLexiconSuggestion.status === "completed"
          ? "这组世界杯博彩召回词已经添加到正式词库，无需重复提交。"
          : "这组世界杯博彩召回词已有待确认建议，请先在现有卡片中预览或完成提交。"
      };
    }
    return {
      id: createId("lexicon-a"),
      role: "assistant",
      kind: "lexicon-suggestion",
      content: "已根据你的要求整理出一组新增召回词建议。你可以调整后再加入词库结构预览。"
    };
  }

  if (isGamblingLexicon && /(世界杯|欧洲杯|赛事|博彩|滚球|召回词|词条)/.test(normalized)) {
    return {
      id: createId("lexicon-a"),
      role: "assistant",
      kind: "lexicon-answer",
      content: "当前词库已覆盖世界杯投注、盘口和滚球场景，共整理出 6 组相关表达。"
    };
  }

  if (/(新增|增加|添加).*(透明衣物|透视展示)|(透明衣物|透视展示).*(新增|增加|添加)/.test(normalized)) {
    return {
      id: createId("rule-a"),
      role: "assistant",
      kind: "new-rule-suggestion",
      content: "已根据你的要求整理出一条完整的新增规则建议。你可以先检查规则内容和适用范围，再决定是否加入规则结构预览。",
      sourceRuleSetIds: [conversation.ruleSetId || "ruleset-erotic"]
    };
  }

  return {
    id: createId("rule-a"),
    role: "assistant",
    kind: "text",
    content: isGlobal
      ? "当前综合问答聚焦私域引流资源检索与覆盖评估。你可以询问相关规则、召回词或专项建设缺口。"
      : isLexicon
        ? "我已优先查看当前黑话库。你可以继续询问现有词条、表达变体或平台适配情况。"
        : "我已优先查看当前审核规则，并完成相关规则检索。你可以继续追问判断条件、豁免场景或修改影响。",
    sourceRuleSetIds: conversation.ruleSetId ? [conversation.ruleSetId] : undefined
  };
}

export function RuleAssistantPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const routeState = (location.state || {}) as RuleAssistantRouteState;
  const {
    ruleSets,
    lexicons,
    conversations,
    candidatePreviews,
    candidateLexiconPreviews,
    activeConversationId,
    expandedRuleSetIds,
    expandedLexiconIds,
    returnLocation,
    setActiveConversationId,
    setExpandedRuleSetIds,
    setExpandedLexiconIds,
    setReturnLocation,
    openRuleSet,
    createConversation,
    createLexiconConversation,
    createResourceConversation,
    discardDraftResource,
    completeResourceCreation,
    updateConversation,
    prepareCandidatePreview,
    prepareLexiconCandidatePreview,
    completeCandidatePreview,
    applyConversationSuggestion,
    updateCandidatePreviewRule
  } = useRuleAssistantWorkspace();

  const [draft, setDraft] = useState("");
  const [drawer, setDrawer] = useState<RuleAssistantDrawerState | null>(null);
  const [activeMenuId, setActiveMenuId] = useState<string | null>(null);
  const [isHeaderMenuOpen, setIsHeaderMenuOpen] = useState(false);
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false);
  const [pendingConversationId, setPendingConversationId] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [suggestionDrafts, setSuggestionDrafts] = useState<Record<string, RiskRule>>({});
  const [newWorkPanel, setNewWorkPanel] = useState<"start" | "resource" | "resource-type" | null>(null);
  const [selectedResource, setSelectedResource] = useState<WorkspaceResourceSelection | null>(null);
  const [selectedResourceType, setSelectedResourceType] = useState<"rule-set" | "lexicon">("rule-set");
  const [composerResources, setComposerResources] = useState<Record<string, WorkspaceResourceSelection | null>>({});
  const [isResourcePickerOpen, setIsResourcePickerOpen] = useState(false);
  const [pendingAttachments, setPendingAttachments] = useState<Record<string, string>>({});
  const [isAttachmentPickerOpen, setIsAttachmentPickerOpen] = useState(false);
  const handledEntryRef = useRef(false);
  const timelineRef = useRef<HTMLDivElement>(null);
  const sidebarScrollRef = useRef<HTMLDivElement>(null);
  const composerInputRef = useRef<HTMLTextAreaElement>(null);
  const attachmentTriggerRef = useRef<HTMLButtonElement>(null);

  const activeConversation = conversations.find((item) => item.id === activeConversationId)
    || conversations[0];
  const activeRuleSet = ruleSets.find((item) => item.id === activeConversation?.ruleSetId);
  const activeLexicon = lexicons.find((item) => item.id === activeConversation?.lexiconId);
  const isBlankConversation = Boolean(
    activeConversation
    && activeConversation.messages.length === 0
    && !activeConversation.ruleSetId
    && !activeConversation.lexiconId
    && !activeConversation.purpose?.startsWith("create-")
  );
  const suggestedPrompt = activeConversation && !isBlankConversation
    ? getSuggestedPrompt(activeConversation)
    : null;
  const pendingAttachmentName = activeConversation ? pendingAttachments[activeConversation.id] : undefined;
  const boundResource: WorkspaceResourceSelection | null = activeConversation?.ruleSetId
    ? { type: "rule-set", id: activeConversation.ruleSetId }
    : activeConversation?.lexiconId
      ? { type: "lexicon", id: activeConversation.lexiconId }
      : null;
  const hasComposerResourceOverride = Boolean(
    activeConversation
    && Object.prototype.hasOwnProperty.call(composerResources, activeConversation.id)
  );
  const composerResource = hasComposerResourceOverride
    ? composerResources[activeConversation.id] || null
    : boundResource;
  const composerResourceName = composerResource?.type === "rule-set"
    ? ruleSets.find((item) => item.id === composerResource.id)?.name
    : composerResource
      ? lexicons.find((item) => item.id === composerResource.id)?.name
      : undefined;
  const composerAttachmentLabel = "添加附件";
  const recentConversations = useMemo(() => conversations
    .map((conversation, index) => ({
      conversation,
      index,
      recency: getConversationRecency(conversation.updatedAt, -index)
    }))
    .sort((left, right) => right.recency - left.recency || left.index - right.index)
    .map(({ conversation }) => conversation), [conversations]);

  useEffect(() => {
    document.title = "知识助手 · 内容巡查研判平台";
  }, []);

  useEffect(() => {
    if (handledEntryRef.current) return;
    handledEntryRef.current = true;
    if (routeState.returnLocation) setReturnLocation(routeState.returnLocation);
    if (routeState.entryRuleSetId) openRuleSet(routeState.entryRuleSetId);
  }, [openRuleSet, routeState.entryRuleSetId, routeState.returnLocation, setReturnLocation]);

  useEffect(() => {
    timelineRef.current?.scrollTo({ top: timelineRef.current.scrollHeight, behavior: "smooth" });
  }, [activeConversationId, activeConversation?.messages.length, pendingConversationId]);

  useEffect(() => {
    sidebarScrollRef.current?.querySelector(".ra-conversation-row.is-active")?.scrollIntoView({ block: "nearest" });
  }, [activeConversationId, activeConversation?.purpose, activeConversation?.ruleSetId, activeConversation?.lexiconId]);

  useEffect(() => {
    if (!toast) return;
    const timeout = window.setTimeout(() => setToast(null), 2400);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  if (!activeConversation) return null;

  const selectConversation = (conversation: RuleAssistantConversation) => {
    setActiveConversationId(conversation.id);
    if (conversation.ruleSetId) {
      setExpandedRuleSetIds(new Set([conversation.ruleSetId]));
      setExpandedLexiconIds(new Set());
    } else if (conversation.lexiconId) {
      setExpandedRuleSetIds(new Set());
      setExpandedLexiconIds(new Set([conversation.lexiconId]));
    }
    setDrawer(null);
    setActiveMenuId(null);
    setIsResourcePickerOpen(false);
    setIsAttachmentPickerOpen(false);
    setIsMobileSidebarOpen(false);
  };

  const startBlankConversation = () => {
    createConversation(null);
    setDraft("");
    setDrawer(null);
    setActiveMenuId(null);
    setIsResourcePickerOpen(false);
    setIsAttachmentPickerOpen(false);
    setIsMobileSidebarOpen(false);
  };

  const bindConversationToResource = (
    conversation: RuleAssistantConversation,
    resource: WorkspaceResourceSelection
  ) => {
    const routedConversation: RuleAssistantConversation = {
      ...conversation,
      ruleSetId: resource.type === "rule-set" ? resource.id : null,
      lexiconId: resource.type === "lexicon" ? resource.id : null,
      purpose: "standard"
    };
    updateConversation(conversation.id, (current) => ({
      ...current,
      ruleSetId: routedConversation.ruleSetId,
      lexiconId: routedConversation.lexiconId,
      purpose: routedConversation.purpose
    }));
    if (resource.type === "rule-set") {
      setExpandedRuleSetIds(new Set([resource.id]));
      setExpandedLexiconIds(new Set());
    } else {
      setExpandedRuleSetIds(new Set());
      setExpandedLexiconIds(new Set([resource.id]));
    }
    return routedConversation;
  };

  const chooseComposerResource = (resource: WorkspaceResourceSelection) => {
    setComposerResources((current) => ({ ...current, [activeConversation.id]: resource }));
    setDraft((current) => current.replace(/@[^@\s]*$/, "").trimEnd());
    setIsResourcePickerOpen(false);
    window.requestAnimationFrame(() => composerInputRef.current?.focus());
  };

  const closeResourcePicker = () => {
    setIsResourcePickerOpen(false);
    window.requestAnimationFrame(() => composerInputRef.current?.focus());
  };

  const chooseMockAttachment = (fileName: string) => {
    setPendingAttachments((current) => ({ ...current, [activeConversation.id]: fileName }));
    setIsAttachmentPickerOpen(false);
    window.requestAnimationFrame(() => composerInputRef.current?.focus());
  };

  const closeMockAttachmentPicker = () => {
    setIsAttachmentPickerOpen(false);
    window.requestAnimationFrame(() => attachmentTriggerRef.current?.focus());
  };

  const clearMockAttachment = () => {
    setPendingAttachments((current) => {
      const next = { ...current };
      delete next[activeConversation.id];
      return next;
    });
    setIsAttachmentPickerOpen(false);
  };

  const openMockAttachmentPicker = () => {
    if (pendingConversationId) return;
    setIsResourcePickerOpen(false);
    setIsAttachmentPickerOpen((current) => {
      const next = !current;
      if (next) {
        window.requestAnimationFrame(() => {
          document.querySelector<HTMLButtonElement>("#mock-attachment-picker [role='menuitem']")?.focus();
        });
      }
      return next;
    });
  };

  const clearComposerResource = () => {
    setComposerResources((current) => ({ ...current, [activeConversation.id]: null }));
    setIsResourcePickerOpen(false);
    window.requestAnimationFrame(() => composerInputRef.current?.focus());
  };

  const getMessageResourceContext = (
    resource: WorkspaceResourceSelection | null
  ): RuleAssistantMessageResourceContext | undefined => {
    if (!resource) return undefined;
    const resourceName = resource.type === "rule-set"
      ? ruleSets.find((item) => item.id === resource.id)?.name
      : lexicons.find((item) => item.id === resource.id)?.name;
    if (!resourceName) return undefined;
    return {
      resourceId: resource.id,
      resourceType: resource.type,
      resourceName
    };
  };

  const appendMessages = (conversationId: string, messages: RuleAssistantMessage[]) => {
    updateConversation(conversationId, (conversation) => ({
      ...conversation,
      title: conversation.title === "新规则对话" || conversation.title === "新综合问答" || conversation.title === "新对话" || conversation.title === "新的词库对话"
        ? (messages.find((message) => message.role === "user")?.content || conversation.title).slice(0, 18)
        : conversation.title,
      updatedAt: currentTime(),
      messages: [...conversation.messages, ...messages]
    }));
  };

  const sendPrompt = (value: string) => {
    const text = value.trim();
    if ((!text && !pendingAttachmentName) || pendingConversationId) return;
    if (pendingAttachmentName) {
      const fileName = pendingAttachmentName;
      clearMockAttachment();
      setDraft("");
      handleMockFile(fileName, text);
      return;
    }
    const inferredResource = composerResource
      ? null
      : inferExistingResource(text, ruleSets, lexicons);
    const messageResource = composerResource || inferredResource;
    if (inferredResource) {
      setComposerResources((current) => ({
        ...current,
        [activeConversation.id]: inferredResource
      }));
    }
    const userMessage: RuleAssistantMessage = {
      id: createId("rule-u"),
      role: "user",
      content: text,
      resourceContext: getMessageResourceContext(messageResource)
    };

    if (isBlankConversation) {
      const creationType = inferResourceCreation(text);
      if (creationType) {
        const conversationId = createResourceConversation(creationType, activeConversation.id);
        appendMessages(conversationId, [userMessage]);
        setDraft("");
        setPendingConversationId(conversationId);
        window.setTimeout(() => {
          appendMessages(conversationId, [{
            id: createId("resource-create-a"),
            role: "assistant",
            kind: "text",
            content: creationType === "lexicon"
              ? "可以，请上传暴恐涉敏召回词表。解析完成后，你可以先核对词条结构再创建词库。"
              : "可以，请上传暴恐内容审核规范。解析完成后，你可以先核对规则结构再创建审核规则。"
          }]);
          setPendingConversationId((current) => current === conversationId ? null : current);
        }, 420);
        return;
      }
    }

    const routedResource = isBlankConversation ? messageResource : null;
    const routedConversation = routedResource
      ? bindConversationToResource(activeConversation, routedResource)
      : activeConversation;
    const replyConversation: RuleAssistantConversation = messageResource
      ? {
          ...routedConversation,
          ruleSetId: messageResource.type === "rule-set" ? messageResource.id : null,
          lexiconId: messageResource.type === "lexicon" ? messageResource.id : null
        }
      : { ...routedConversation, ruleSetId: null, lexiconId: null };
    const conversationId = routedConversation.id;
    appendMessages(conversationId, [userMessage]);
    setDraft("");
    setPendingConversationId(conversationId);
    window.setTimeout(() => {
      appendMessages(conversationId, [buildMockReply(text, replyConversation)]);
      setPendingConversationId((current) => current === conversationId ? null : current);
    }, 520);
  };

  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      (event.key === "Backspace" || event.key === "Delete")
      && composerResource
      && event.currentTarget.selectionStart === 0
      && event.currentTarget.selectionEnd === 0
    ) {
      event.preventDefault();
      clearComposerResource();
      return;
    }
    if (event.key === "Escape" && (isResourcePickerOpen || isAttachmentPickerOpen)) {
      event.preventDefault();
      if (isResourcePickerOpen) closeResourcePicker();
      else closeMockAttachmentPicker();
      return;
    }
    if (event.key === "ArrowDown" && isResourcePickerOpen) {
      event.preventDefault();
      document.querySelector<HTMLButtonElement>("#resource-mention-picker [role='menuitem']")?.focus();
      return;
    }
    if (event.key === "Enter" && !event.shiftKey && isResourcePickerOpen) {
      event.preventDefault();
      return;
    }
    if (event.key === "Tab" && !event.shiftKey && !draft && suggestedPrompt) {
      event.preventDefault();
      setDraft(suggestedPrompt);
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendPrompt(draft);
    }
  };

  const handleMockFile = (fileName: string, prompt: string) => {
    if (pendingConversationId) return;
    const messageResourceContext = getMessageResourceContext(composerResource);
    let conversationForFile = isBlankConversation && composerResource
      ? bindConversationToResource(activeConversation, composerResource)
      : activeConversation;

    const createsRuleSet = fileName === mockTerrorRuleFileName;
    const createsLexicon = fileName === mockTerrorLexiconFileName;

    if (createsLexicon && conversationForFile.purpose !== "create-lexicon") {
      const resourceId = createId("lexicon-draft");
      createResourceConversation("lexicon", conversationForFile.id, resourceId);
      conversationForFile = {
        ...conversationForFile,
        ruleSetId: null,
        lexiconId: resourceId,
        purpose: "create-lexicon",
        scenario: "create-terror-lexicon"
      };
    } else if (createsRuleSet && conversationForFile.purpose !== "create-rule-set") {
      const resourceId = createId("ruleset-draft");
      createResourceConversation("rule-set", conversationForFile.id, resourceId);
      conversationForFile = {
        ...conversationForFile,
        ruleSetId: resourceId,
        lexiconId: null,
        purpose: "create-rule-set",
        scenario: "create-terror-rule-set"
      };
    } else if (fileName === mockRuleFileName && !conversationForFile.ruleSetId) {
      conversationForFile = bindConversationToResource(conversationForFile, { type: "rule-set", id: "ruleset-erotic" });
    }

    const conversationId = conversationForFile.id;
    if (createsLexicon) {
      const lexiconPreviewId = createId("lexicon-create-preview");
      const lexiconDraftId = conversationForFile.lexiconId || createId("lexicon-draft");
      appendMessages(conversationId, [{
        id: createId("lexicon-u-file"),
        role: "user",
        content: prompt || "请整理这份词表，创建一套暴恐涉敏黑话库。",
        resourceContext: messageResourceContext,
        fileName
      }]);
      setPendingConversationId(conversationId);
      window.setTimeout(() => {
        appendMessages(conversationId, [{
          id: createId("lexicon-a-file"),
          role: "assistant",
          kind: "lexicon-file-analysis",
          content: "已根据文件整理出一套完整黑话库结构。",
          fileName,
          previewId: lexiconPreviewId,
          lexiconDraft: createTerrorLexiconCandidate(lexiconDraftId)
        }]);
        setPendingConversationId((current) => current === conversationId ? null : current);
      }, 720);
      return;
    }
    const isCreatingRuleSet = createsRuleSet;
    const previewId = prepareCandidatePreview({
      conversationId,
      sourceRuleSetId: conversationForFile.ruleSetId,
      createOnly: isCreatingRuleSet,
      fileName
    });
    appendMessages(conversationId, [{
      id: createId("rule-u-file"),
      role: "user",
      content: prompt || (isCreatingRuleSet
        ? "请解析这份规范，并整理成完整的暴恐风险规则结构。"
        : importRuleFilePrompt),
      resourceContext: messageResourceContext,
      fileName
    }]);
    setPendingConversationId(conversationId);
    window.setTimeout(() => {
      appendMessages(conversationId, [{
        id: createId("rule-a-file"),
        role: "assistant",
        kind: "file-analysis",
        content: "文件解析完成。已根据文件整理出一套完整规则结构。",
        fileName,
        previewId,
        sourceRuleSetIds: conversationForFile.ruleSetId ? [conversationForFile.ruleSetId] : undefined
      }]);
      setPendingConversationId((current) => current === conversationId ? null : current);
    }, 720);
  };

  const updateMessageStatus = (messageId: string, status: RuleAssistantMessage["status"]) => {
    updateConversation(activeConversation.id, (conversation) => ({
      ...conversation,
      messages: conversation.messages.map((message) => (
        message.id === messageId ? { ...message, status } : message
      ))
    }));
  };

  const cancelFileImport = (message: RuleAssistantMessage) => {
    updateMessageStatus(message.id, "cancelled");
    if (message.kind === "file-analysis" && message.previewId) {
      completeCandidatePreview(message.previewId, "cancelled");
    }
    if (message.kind === "lexicon-file-analysis") {
      const resourceId = message.lexiconDraft?.id
        || (activeConversation.purpose === "create-lexicon" ? activeConversation.lexiconId : null);
      if (resourceId) {
        discardDraftResource("lexicon", resourceId, activeConversation.id);
      }
    }
    if (message.kind === "file-analysis" && activeConversation.purpose === "create-rule-set" && activeConversation.ruleSetId) {
      discardDraftResource("rule-set", activeConversation.ruleSetId, activeConversation.id);
    }
  };

  const getSuggestionRule = (message: RuleAssistantMessage): RiskRule => {
    const fallback = message.kind === "new-rule-suggestion"
      ? addedTransparentClothingRule
      : {
          ...originalLocalCloseupRule,
          suggestedLevel: "高风险" as const,
          content: modifiedLocalCloseupContent
        };
    const rule = suggestionDrafts[message.id] || fallback;
    return { ...rule, applicationStages: [...rule.applicationStages] };
  };

  const applySuggestionToPreview = (message: RuleAssistantMessage, kind: "modify" | "add") => {
    const previewId = applyConversationSuggestion({
      conversationId: activeConversation.id,
      sourceRuleSetId: activeConversation.ruleSetId || "ruleset-erotic",
      kind,
      candidateRule: getSuggestionRule(message)
    });
    updateConversation(activeConversation.id, (conversation) => ({
      ...conversation,
      updatedAt: currentTime(),
      messages: [
        ...conversation.messages.map((item) => item.id === message.id ? { ...item, status: "applied" as const, previewId } : item),
        {
          id: createId("rule-a-preview-updated"),
          role: "assistant" as const,
          kind: "text" as const,
          content: "已更新规则结构预览，尚未应用到正式审核规则。"
        }
      ]
    }));
    navigate(`/rule-assistant/import-preview/${encodeURIComponent(previewId)}`);
  };

  const openLexiconSuggestionPreview = (message: RuleAssistantMessage) => {
    if (message.previewId) {
      navigate(`/rule-assistant/lexicon-preview/${encodeURIComponent(message.previewId)}`);
      return;
    }
    const sourceLexiconId = activeConversation.lexiconId || "recall-gambling";
    const previewId = prepareLexiconCandidatePreview({
      conversationId: activeConversation.id,
      sourceLexiconId
    });
    updateConversation(activeConversation.id, (conversation) => ({
      ...conversation,
      updatedAt: currentTime(),
      messages: conversation.messages.map((item) => item.id === message.id ? {
        ...item,
        status: "applied" as const,
        previewId,
        sourceLexiconIds: [sourceLexiconId]
      } : item)
    }));
    navigate(`/rule-assistant/lexicon-preview/${encodeURIComponent(previewId)}`);
  };

  const beginConversationForResource = () => {
    if (!selectedResource) return;
    if (selectedResource.type === "rule-set") {
      setExpandedRuleSetIds(new Set([selectedResource.id]));
      setExpandedLexiconIds(new Set());
      createConversation(selectedResource.id);
    } else {
      createLexiconConversation(selectedResource.id);
    }
    setNewWorkPanel(null);
    setSelectedResource(null);
    setIsMobileSidebarOpen(false);
  };

  const beginResourceCreation = () => {
    createResourceConversation(selectedResourceType);
    setNewWorkPanel(null);
    setIsMobileSidebarOpen(false);
  };

  const finishMockResourceCreation = () => {
    const type = activeConversation.purpose === "create-lexicon" ? "lexicon" : "rule-set";
    completeResourceCreation(activeConversation.id, type);
    setToast(type === "lexicon" ? "已创建暴恐涉敏黑话库" : "已创建暴恐审核规则");
  };

  const openResourceManager = (type: "rule-set" | "lexicon", resourceId?: string | null) => {
    if (type === "lexicon") {
      navigate("/rule-assistant/lexicons");
      return;
    }
    navigate("/rule-assistant/rulesets", {
      state: resourceId ? { entryRuleSetId: resourceId } : undefined
    });
  };

  const handleReturn = () => {
    if (!returnLocation) {
      navigate("/investigation");
      return;
    }
    navigate(`${returnLocation.pathname}${returnLocation.search || ""}`, {
      state: {
        restoreInvestigationState: {
          activeSessionId: returnLocation.activeSessionId,
          activeSubView: returnLocation.activeSubView
        },
        restoreScrollTop: returnLocation.scrollTop || 0
      }
    });
  };

  const renderSources = (message: RuleAssistantMessage) => {
    if (!message.sourceRuleSetIds?.length && !message.sourceLexiconIds?.length) return null;
    const isResourceAnswer = message.kind === "resource-inventory" || message.kind === "resource-coverage";
    return (
      <div className="ra-sources">
        <span>{isResourceAnswer ? "资源证据" : "参考规则"}</span>
        <div>
          {message.sourceRuleSetIds?.map((ruleSetId) => {
            const ruleSet = ruleSets.find((item) => item.id === ruleSetId);
            if (!ruleSet) return null;
            return (
              <button
                type="button"
                key={ruleSetId}
                onClick={() => setDrawer({ type: "rule", ruleSetId })}
              >
                <FileText size={13} />
                {ruleSet.name}
              </button>
            );
          })}
          {message.sourceLexiconIds?.map((lexiconId) => {
            const lexicon = lexicons.find((item) => item.id === lexiconId);
            if (!lexicon) return null;
            return (
              <button
                type="button"
                key={lexiconId}
                onClick={() => setDrawer({ type: "lexicon", lexiconId })}
              >
                <Tags size={13} />
                {lexicon.name}
              </button>
            );
          })}
        </div>
      </div>
    );
  };

  const renderAssistantContent = (message: RuleAssistantMessage) => {
    if (message.kind === "resource-inventory") {
      return (
        <>
          <p className="ra-answer-lead">{message.content}</p>
          <div className="ra-compact-resource-answer">
            <section className="ra-compact-basis-group">
              <h4><FileText size={14} />研判规则</h4>
              <div className="ra-compact-basis-item">
                <strong>赌博博彩审核规则</strong>
                <p>覆盖二维码、外部链接、第三方联系方式、评论区私聊等导流行为。</p>
              </div>
            </section>
            <section className="ra-compact-basis-group">
              <h4><Tags size={14} />黑话库</h4>
              <div className="ra-compact-basis-item">
                <strong>通用涉赌博彩黑话库</strong>
                <p>包含“外围盘口”“代理开户”等博彩场景引流词。</p>
              </div>
              <div className="ra-compact-basis-item">
                <strong>诈骗黑色产业链黑话库</strong>
                <p>包含“高额返利群”“内幕荐股群”等诈骗场景引流词。</p>
              </div>
            </section>
            <p className="ra-compact-conclusion">现有资源可以覆盖赌博、诈骗场景中的私域引流，但暂时没有一套面向所有场景的通用私域引流资源。</p>
          </div>
          <div className="ra-answer-actions">
            <button type="button" onClick={() => navigate("/rule-assistant/structure/ruleset-gambling")}><ListTree size={14} />查看相关规则</button>
          </div>
        </>
      );
    }

    if (message.kind === "resource-coverage") {
      return (
        <>
          <p className="ra-answer-lead">{message.content}</p>
          <div className="ra-compact-assessment">
            <div>
              <strong>规则方面</strong>
              <p>现有规则主要面向赌博场景，缺少跨场景统一的二维码、外链和联系方式引流判断。</p>
            </div>
            <div>
              <strong>召回方面</strong>
              <p>现有词库缺少“主页见”“加 V”“扫码进群”“私信发你”等通用表达及其谐音、拆字变体。</p>
            </div>
            <div className="is-advice">
              <strong>建议</strong>
              <p>建议补充一套通用的二维码与联系方式引流规则和召回词，现有博彩、诈骗资源继续作为场景补充。</p>
            </div>
          </div>
          <div className="ra-answer-actions">
            <button type="button" onClick={() => navigate("/rule-assistant/structure/ruleset-gambling")}><ListTree size={14} />查看相关规则</button>
          </div>
        </>
      );
    }

    if (message.kind === "ruleset-summary") {
      return (
        <>
          <p>{message.content}</p>
          <div className="ra-answer-actions">
            <button type="button" onClick={() => navigate(`/rule-assistant/structure/${encodeURIComponent(message.sourceRuleSetIds?.[0] || activeConversation.ruleSetId || "ruleset-erotic")}`)}><ListTree size={14} />查看相关规则</button>
          </div>
        </>
      );
    }

    if (message.kind === "high-risk-rules") {
      return (
        <>
          <p>{message.content}</p>
          <div className="ra-answer-actions">
            <button type="button" onClick={() => navigate(`/rule-assistant/structure/${encodeURIComponent(message.sourceRuleSetIds?.[0] || activeConversation.ruleSetId || "ruleset-erotic")}`)}><ListTree size={14} />查看相关规则</button>
          </div>
        </>
      );
    }

    if (message.kind === "lexicon-answer") {
      return (
        <>
          <p>{message.content}</p>
          <div className="ra-lexicon-groups">
            <section><span>赛事名称</span><strong>世界杯投注 · 世界杯盘口</strong></section>
            <section><span>实时玩法</span><strong>世界杯滚球 · 世界杯走地</strong></section>
            <section><span>盘口表达</span><strong>世界杯比分盘 · 世界杯外围</strong></section>
          </div>
          <div className="ra-sources"><span>参考词库</span><div><button type="button"><Tags size={13} />{activeLexicon?.name || "通用涉赌博彩黑话库"}</button></div></div>
        </>
      );
    }

    if (message.kind === "lexicon-suggestion") {
      if (message.status === "cancelled") return <p className="ra-cancelled-copy">已取消这组新增词建议，词库结构预览未发生变化。</p>;
      if (message.status === "completed") {
        return (
          <div className="ra-completed-result">
            <div className="ra-success-state"><CheckCircle2 size={18} /><div><strong>已添加到正式词库</strong><p>新增词条及其查询类型、表达变体已生效。</p></div></div>
            <div className="ra-card-actions ra-success-actions">
              <button type="button" onClick={() => openResourceManager("lexicon")}><Tags size={14} />回到黑话库</button>
            </div>
          </div>
        );
      }
      if (message.status === "applied") {
        const preview = message.previewId ? candidateLexiconPreviews[message.previewId] : undefined;
        const pendingCount = preview?.addedTermIds.length ?? gamblingLexiconSuggestedTerms.length;
        return (
          <div className="ra-completed-result">
            <div className="ra-success-state"><CheckCircle2 size={18} /><div><strong>已添加到词库结构预览</strong><p>新增的 {pendingCount} 个词条尚未应用到正式词库。</p></div></div>
            <div className="ra-card-actions ra-success-actions">
              <button type="button" className="is-primary" onClick={() => openLexiconSuggestionPreview(message)}><ListTree size={14} />查看词库结构预览</button>
            </div>
          </div>
        );
      }
      return (
        <div className="ra-proposal-card ra-lexicon-suggestion-card">
          <div className="ra-card-kicker"><Tags size={15} />新增召回词建议</div>
          <div className="ra-field-row"><span>目标词库</span><strong>{activeLexicon?.name || "通用涉赌博彩黑话库"}</strong></div>
          <div className="ra-lexicon-suggestion-list" role="table" aria-label="新增召回词建议">
            <div className="ra-lexicon-suggestion-head" role="row"><span>主词</span><span>查询类型</span><span>表达变体</span></div>
            {gamblingLexiconSuggestedTerms.map((term) => (
              <div className="ra-lexicon-suggestion-row" role="row" key={term.id}>
                <strong>{term.primary}</strong>
                <span className={`ra-query-type-badge${term.queryType === "标签" ? " is-tag" : ""}`}>{term.queryType}</span>
                <p>{term.variants}</p>
              </div>
            ))}
          </div>
          <p className="ra-lexicon-preview-note">可在预览页逐条调整主词、变体、查询类型和启停状态。</p>
          <div className="ra-card-actions">
            <button type="button" onClick={() => updateMessageStatus(message.id, "cancelled")}>取消</button>
            <button type="button" className="is-primary" onClick={() => openLexiconSuggestionPreview(message)}><ListTree size={14} />添加到词库结构预览</button>
          </div>
        </div>
      );
    }

    if (message.kind === "lexicon-file-analysis") {
      const isTerrorLexicon = message.fileName === mockTerrorLexiconFileName;
      const isCompleted = message.status === "completed" || message.status === "applied";
      if (message.status === "cancelled") {
        return (
          <div className="ra-cancelled-state">
            <X size={18} />
            <div><strong>已取消本次导入</strong><p>文件解析结果未应用，未创建正式黑话库。</p></div>
          </div>
        );
      }
      return (
        <div className="ra-file-result-card">
          <div className="ra-file-result-head"><FileCheck2 size={19} /><div><strong>{isTerrorLexicon ? "文件解析完成" : "词表解析完成"}</strong><span>{message.fileName}</span></div></div>
          <p className="ra-file-summary">{isTerrorLexicon ? "已根据文件整理出一套完整黑话库结构：" : message.content}</p>
          <dl className="ra-file-metrics">
            <div><dt>词条分组</dt><dd>4 个</dd></div>
            <div><dt>{isTerrorLexicon ? "核心词" : "召回词"}</dt><dd>38 个</dd></div>
            <div><dt>表达变体</dt><dd>{isTerrorLexicon ? "21 个" : "12 条"}</dd></div>
          </dl>
          {isTerrorLexicon ? <p className={isCompleted ? "ra-file-applied" : "ra-file-pending"}>{isCompleted ? "已创建黑话库，可继续在当前对话中补充词条。" : "当前结构尚未应用。"}</p> : null}
          {isTerrorLexicon && !isCompleted ? (
            <div className="ra-card-actions">
              <button type="button" onClick={() => cancelFileImport(message)}><X size={14} />取消</button>
              <button type="button" className="is-primary" onClick={() => navigate(`/rule-assistant/lexicon-preview/${encodeURIComponent(message.previewId || activeConversation.id)}`)}><ListTree size={14} />查看词库结构预览</button>
            </div>
          ) : null}
          {isTerrorLexicon && isCompleted ? (
            <div className="ra-card-actions ra-success-actions">
              <button type="button" onClick={() => openResourceManager("lexicon")}><Tags size={14} />回到黑话库</button>
            </div>
          ) : null}
        </div>
      );
    }

    if (message.kind === "rule-change-suggestion") {
      if (message.status === "cancelled") {
        return <p className="ra-cancelled-copy">已取消这项修改建议，规则结构预览未发生变化。</p>;
      }
      if (message.status === "completed") {
        return (
          <div className="ra-completed-result">
            <div className="ra-success-state">
              <CheckCircle2 size={18} />
              <div><strong>已应用到正式审核规则</strong><p>局部露拍的调整已写入当前审核规则。</p></div>
            </div>
            <div className="ra-card-actions ra-success-actions">
              <button type="button" onClick={() => openResourceManager("rule-set", activeConversation.ruleSetId)}><BookOpenText size={14} />回到审核规则</button>
            </div>
          </div>
        );
      }
      if (message.status === "applied") {
        return (
          <div className="ra-success-state">
            <CheckCircle2 size={18} />
            <div>
              <strong>已加入规则结构预览</strong>
              <p>局部露拍已调整，尚未应用到正式审核规则。可继续调整，保存后会同步更新当前预览。</p>
              <div className="ra-card-actions ra-success-actions">
                {message.previewId ? <button type="button" onClick={() => navigate(`/rule-assistant/import-preview/${encodeURIComponent(message.previewId!)}`)}>查看规则结构预览</button> : null}
                <button type="button" className="is-primary" onClick={() => setDrawer({ type: "candidate-editor", payloadId: message.id })}>继续调整</button>
              </div>
            </div>
          </div>
        );
      }
      const rule = getSuggestionRule(message);
      return (
        <div className="ra-proposal-card ra-rule-suggestion-card">
          <div className="ra-suggestion-title">
            <strong>{rule.name}</strong>
            <div className="ra-risk-transition" aria-label={`风险等级由${originalLocalCloseupRule.suggestedLevel}调整为${rule.suggestedLevel}`}>
              <span>{originalLocalCloseupRule.suggestedLevel}</span><ChevronRight size={14} /><strong>{rule.suggestedLevel}</strong>
            </div>
          </div>
          <div className="ra-rule-change-comparison">
            <div className="is-before"><span>修改前</span><p>{originalLocalCloseupRule.content}</p></div>
            <div className="is-after"><span>修改后</span><p>{rule.content}</p></div>
          </div>
          <div className="ra-suggestion-meta">
            <div><span>所属风险类型</span><strong>身体隐私部位暴露</strong></div>
            <div><span>应用阶段</span><p>{rule.applicationStages.join(" · ")}</p></div>
          </div>
          <div className="ra-card-actions ra-suggestion-card-actions">
            <button type="button" onClick={() => setDrawer({ type: "suggestion-diff", payloadId: message.id })}>查看完整差异</button>
            <button type="button" className="is-tertiary" onClick={() => updateMessageStatus(message.id, "cancelled")}>取消</button>
            <button type="button" className="is-primary" onClick={() => applySuggestionToPreview(message, "modify")}><ListTree size={14} />应用到规则结构预览</button>
          </div>
        </div>
      );
    }

    if (message.kind === "new-rule-suggestion") {
      if (message.status === "cancelled") {
        return <p className="ra-cancelled-copy">已取消这条新增建议，规则结构预览未发生变化。</p>;
      }
      if (message.status === "completed") {
        return (
          <div className="ra-completed-result">
            <div className="ra-success-state">
              <CheckCircle2 size={18} />
              <div><strong>已应用到正式审核规则</strong><p>透明衣物透视展示已写入当前审核规则。</p></div>
            </div>
            <div className="ra-card-actions ra-success-actions">
              <button type="button" onClick={() => openResourceManager("rule-set", activeConversation.ruleSetId)}><BookOpenText size={14} />回到审核规则</button>
            </div>
          </div>
        );
      }
      if (message.status === "applied") {
        return (
          <div className="ra-success-state">
            <CheckCircle2 size={18} />
            <div>
              <strong>已加入规则结构预览</strong>
              <p>透明衣物透视展示已新增，尚未应用到正式审核规则。可继续调整，保存后会同步更新当前预览。</p>
              <div className="ra-card-actions ra-success-actions">
                {message.previewId ? <button type="button" onClick={() => navigate(`/rule-assistant/import-preview/${encodeURIComponent(message.previewId!)}`)}>查看规则结构预览</button> : null}
                <button type="button" className="is-primary" onClick={() => setDrawer({ type: "candidate-editor", payloadId: message.id })}>继续调整</button>
              </div>
            </div>
          </div>
        );
      }
      const rule = getSuggestionRule(message);
      return (
        <div className="ra-proposal-card ra-rule-suggestion-card">
          <div className="ra-suggestion-title"><strong>{rule.name}</strong><span>{rule.suggestedLevel}</span></div>
          <p className="ra-suggestion-summary">{rule.content}</p>
          <div className="ra-suggestion-meta">
            <div><span>所属风险类型</span><strong>身体隐私部位暴露</strong></div>
            <div><span>应用阶段</span><p>{rule.applicationStages.join(" · ")}</p></div>
          </div>
          <div className="ra-card-actions ra-suggestion-card-actions">
            <button type="button" onClick={() => setDrawer({ type: "candidate-editor", payloadId: message.id })}>查看并调整</button>
            <button type="button" className="is-tertiary" onClick={() => updateMessageStatus(message.id, "cancelled")}>取消</button>
            <button type="button" className="is-primary" onClick={() => applySuggestionToPreview(message, "add")}><ListTree size={14} />添加到规则结构预览</button>
          </div>
        </div>
      );
    }

    if (message.kind === "file-analysis") {
      const isCompleted = message.status === "applied" || message.status === "completed";
      const preview = message.previewId ? candidatePreviews[message.previewId] : undefined;
      const categoryCount = preview?.ruleSet.categories.length || 4;
      const ruleCount = preview?.ruleSet.categories.reduce((total, category) => total + category.rules.length, 0) || 13;
      const exemptionCount = preview?.ruleSet.generalExemptions.length || 6;
      const isCreatingRuleSet = preview?.mode === "create-only";
      if (message.status === "cancelled") {
        return (
          <div className="ra-cancelled-state">
            <X size={18} />
            <div><strong>已取消本次导入</strong><p>文件解析结果未应用，未创建或更新正式审核规则。</p></div>
          </div>
        );
      }
      return (
        <div className="ra-file-result-card">
          <div className="ra-file-result-head"><FileCheck2 size={19} /><div><strong>文件解析完成</strong><span>{message.fileName}</span></div></div>
          <p className="ra-file-summary">已根据文件整理出一套完整规则结构：</p>
          <dl className="ra-file-metrics"><div><dt>风险分类</dt><dd>{categoryCount} 个</dd></div><div><dt>风险规则</dt><dd>{ruleCount} 条</dd></div><div><dt>通用豁免</dt><dd>{exemptionCount} 条</dd></div></dl>
          <p className={isCompleted ? "ra-file-applied" : "ra-file-pending"}>{isCompleted ? isCreatingRuleSet ? "已创建暴恐审核规则。" : "已完成所选应用方式，可继续新增或修改规则。" : isCreatingRuleSet ? "当前结构尚未应用。" : "当前结构尚未应用，可继续新增或修改后统一确认。"}</p>
          <div className="ra-card-actions">
            {isCompleted ? (
              <button type="button" onClick={() => openResourceManager("rule-set", preview?.sourceRuleSetId || activeConversation.ruleSetId)}><BookOpenText size={14} />回到审核规则</button>
            ) : (
              <>
                <button type="button" onClick={() => cancelFileImport(message)}><X size={14} />取消</button>
                <button type="button" className="is-primary" disabled={!message.previewId} onClick={() => message.previewId && navigate(`/rule-assistant/import-preview/${encodeURIComponent(message.previewId)}`)}><ListTree size={14} />查看规则结构预览</button>
              </>
            )}
          </div>
        </div>
      );
    }

    return <><p>{message.content}</p>{renderSources(message)}</>;
  };

  return (
    <div className="ra-workspace">
      <aside className={`ra-sidebar${isMobileSidebarOpen ? " is-mobile-open" : ""}`} aria-label="知识助手工作区导航">
        <div className="ra-sidebar-head">
          <div className="ra-brand"><span><ShieldCheck size={18} /></span><strong>知识助手</strong></div>
          <nav className="ra-primary-nav" aria-label="知识助手固定导航">
            <button type="button" className="is-new" onClick={startBlankConversation}><SquarePen size={17} />新建对话</button>
            <button type="button" className="is-ruleset" onClick={() => { navigate("/rule-assistant/rulesets"); setIsMobileSidebarOpen(false); }}><BookOpenText size={17} />审核规则</button>
            <button type="button" className="is-lexicon" onClick={() => { navigate("/rule-assistant/lexicons"); setIsMobileSidebarOpen(false); }}><Tags size={17} />黑话库</button>
          </nav>
        </div>

        <div className="ra-sidebar-scroll" ref={sidebarScrollRef}>
          <div className="ra-history-heading">最近对话</div>
          <section className="ra-sidebar-section ra-recent-conversations" aria-label="最近对话">
            {recentConversations.map((conversation) => (
              <ConversationRow
                key={conversation.id}
                conversation={conversation}
                isActive={conversation.id === activeConversation.id}
                isMenuOpen={activeMenuId === conversation.id}
                onSelect={() => selectConversation(conversation)}
                onToggleMenu={() => setActiveMenuId((current) => current === conversation.id ? null : conversation.id)}
                onMockAction={(label) => { setActiveMenuId(null); setToast(`${label}功能暂未开放`); }}
              />
            ))}
          </section>
        </div>

        <div className="ra-sidebar-foot">
          <button type="button" onClick={handleReturn}><ArrowLeft size={16} />返回调查工作区</button>
        </div>
      </aside>

      <main className={`ra-main${drawer ? " has-drawer" : ""}`}>
        <header className="ra-main-header">
          <button type="button" className="ra-mobile-menu" aria-label="打开知识助手导航" onClick={() => setIsMobileSidebarOpen(true)}><Menu size={18} /></button>
          <div className="ra-title-block">
            <h1>{isBlankConversation ? "新对话" : activeConversation.ruleSetId || activeConversation.lexiconId || activeConversation.purpose?.startsWith("create-") ? activeConversation.title : "知识资源综合问答"}</h1>
            {activeRuleSet ? <p>{activeRuleSet.name}</p> : activeLexicon ? <p>{activeLexicon.name}</p> : null}
          </div>
          <div className="ra-header-actions">
            {activeRuleSet && activeConversation.purpose !== "create-rule-set" ? <button type="button" onClick={() => navigate(`/rule-assistant/structure/${encodeURIComponent(activeRuleSet.id)}`)}><ListTree size={15} />查看规则结构</button> : null}
            <div className="ra-header-menu-wrap">
              <button type="button" className="ra-icon-only" aria-label="更多操作" title="更多" onClick={() => setIsHeaderMenuOpen((current) => !current)}><MoreHorizontal size={18} /></button>
              {isHeaderMenuOpen ? (
                <div className="ra-popover-menu ra-header-popover">
                  <button type="button" onClick={() => { setToast("重命名功能暂未开放"); setIsHeaderMenuOpen(false); }}><Pencil size={14} />重命名</button>
                  <button type="button" onClick={() => { setToast("删除对话功能暂未开放"); setIsHeaderMenuOpen(false); }}><Trash2 size={14} />删除对话</button>
                </div>
              ) : null}
            </div>
          </div>
        </header>

        <div className="ra-timeline" ref={timelineRef}>
          <div className="ra-timeline-inner">
            {activeConversation.messages.length === 0 ? (
              <EmptyConversation
                resourceType={isBlankConversation ? "blank"
                  : activeConversation.purpose === "create-rule-set" ? "create-rule-set"
                  : activeConversation.purpose === "create-lexicon" ? "create-lexicon"
                    : activeConversation.lexiconId ? "lexicon"
                      : activeConversation.ruleSetId ? "rule-set" : "global"}
                resourceName={activeRuleSet?.name || activeLexicon?.name}
                onPrompt={sendPrompt}
                onFile={openMockAttachmentPicker}
                onCreate={finishMockResourceCreation}
              />
            ) : (
              activeConversation.messages.map((message) => {
                if (message.role === "user") {
                  const ResourceContextIcon = message.resourceContext?.resourceType === "lexicon"
                    ? Tags
                    : BookOpenText;
                  return (
                    <div className="ra-user-message" key={message.id}>
                      {message.fileName ? <div className="ra-file-chip"><FileText size={15} /><span>{message.fileName}</span></div> : null}
                      <p>
                        {message.resourceContext ? (
                          <span className={`ra-message-resource-prefix is-${message.resourceContext.resourceType}`}>
                            <ResourceContextIcon size={14} aria-hidden="true" />
                            <span>{message.resourceContext.resourceName}</span>
                          </span>
                        ) : null}
                        <span>{message.content}</span>
                      </p>
                    </div>
                  );
                }

                const isSplitSuggestion = (
                  message.kind === "new-rule-suggestion" || message.kind === "rule-change-suggestion"
                ) && !message.status;
                return (
                  <article className={`ra-assistant-message${isSplitSuggestion ? " is-split-suggestion" : ""}`} key={message.id}>
                    {isSplitSuggestion ? (
                      <div className="ra-assistant-intro-card">
                        <div className="ra-assistant-meta"><Bot size={16} />知识助手</div>
                        <p>{message.content}</p>
                      </div>
                    ) : <div className="ra-assistant-meta"><Bot size={16} />知识助手</div>}
                    <div className="ra-message-content">{renderAssistantContent(message)}</div>
                  </article>
                );
              })
            )}
            {pendingConversationId === activeConversation.id ? (
              <div className="ra-thinking"><LoaderCircle size={16} />正在整理规则…</div>
            ) : null}
          </div>
        </div>

        <div className="ra-composer-shell">
          <div className="ra-composer-stack">
            {pendingAttachmentName ? (
              <div className="ra-composer-context-row">
                <div className="ra-composer-attachment">
                  <span className="ra-composer-attachment-icon"><FileText size={17} /></span>
                  <span className="ra-composer-attachment-copy"><strong>{pendingAttachmentName}</strong><small>{pendingAttachmentName.endsWith(".xlsx") ? "XLSX" : "DOCX"}</small></span>
                  <button type="button" aria-label={`移除附件${pendingAttachmentName}`} onClick={clearMockAttachment}><X size={13} /></button>
                </div>
              </div>
            ) : null}
            {isResourcePickerOpen ? (
              <ResourceMentionPicker
                ruleSets={ruleSets}
                lexicons={lexicons}
                onClose={closeResourcePicker}
                onSelect={chooseComposerResource}
              />
            ) : null}
            {isAttachmentPickerOpen ? (
              <MockAttachmentPicker
                files={mockAttachments}
                onClose={closeMockAttachmentPicker}
                onSelect={chooseMockAttachment}
              />
            ) : null}
            <div className="ra-composer">
              <button
                type="button"
                className="ra-resource-mention-trigger"
                title="选择工作资源"
                aria-label="选择工作资源"
                aria-haspopup="menu"
                aria-expanded={isResourcePickerOpen}
                aria-controls="resource-mention-picker"
                onClick={() => {
                  setIsAttachmentPickerOpen(false);
                  setIsResourcePickerOpen((current) => !current);
                }}
              ><AtSign size={18} /></button>
              <div className="ra-composer-editor">
                {composerResource && composerResourceName ? (
                  <span
                    className={`ra-inline-resource-mention is-${composerResource.type}`}
                    tabIndex={0}
                    aria-label={`当前资源：${composerResourceName}。按 Backspace 或 Delete 移除`}
                    onKeyDown={(event) => {
                      if (event.key !== "Backspace" && event.key !== "Delete") return;
                      event.preventDefault();
                      clearComposerResource();
                    }}
                  >@{composerResourceName}</span>
                ) : null}
                <textarea
                  ref={composerInputRef}
                  rows={1}
                  value={draft}
                  onChange={(event) => {
                    const nextValue = event.target.value;
                    setDraft(nextValue);
                    setIsResourcePickerOpen(nextValue.includes("@"));
                  }}
                  onKeyDown={handleComposerKeyDown}
                  placeholder={isBlankConversation ? "提问或 @ 选择资源…" : suggestedPrompt ? `试着问我：${suggestedPrompt}` : "询问、比较或修改知识资源……"}
                  aria-label="知识助手输入框"
                  aria-keyshortcuts={suggestedPrompt ? "Tab" : undefined}
                />
              </div>
              {!draft && suggestedPrompt && !pendingConversationId ? (
                <span className="ra-tab-hint" aria-hidden="true"><kbd>Tab</kbd><span>填入建议</span></span>
              ) : null}
              <div className="ra-composer-actions">
                <button ref={attachmentTriggerRef} type="button" className="ra-attach" onClick={openMockAttachmentPicker} title={composerAttachmentLabel} aria-label={composerAttachmentLabel} aria-haspopup="menu" aria-expanded={isAttachmentPickerOpen} aria-controls="mock-attachment-picker"><Paperclip size={18} /></button>
                <button type="button" className="ra-send" onClick={() => sendPrompt(draft)} disabled={(!draft.trim() && !pendingAttachmentName) || Boolean(pendingConversationId)} aria-label="发送"><Send size={15} /></button>
              </div>
            </div>
          </div>
        </div>
      </main>

      {isMobileSidebarOpen ? <button type="button" className="ra-mobile-backdrop" aria-label="关闭知识助手导航" onClick={() => setIsMobileSidebarOpen(false)} /> : null}

      {newWorkPanel ? (
        <NewWorkModal
          view={newWorkPanel}
          ruleSets={ruleSets}
          lexicons={lexicons}
          selectedResource={selectedResource}
          selectedResourceType={selectedResourceType}
          onClose={() => { setNewWorkPanel(null); setSelectedResource(null); }}
          onViewChange={setNewWorkPanel}
          onSelectResource={setSelectedResource}
          onSelectResourceType={setSelectedResourceType}
          onCreateGlobal={() => { createConversation(null); setNewWorkPanel(null); setIsMobileSidebarOpen(false); }}
          onCreateConversation={beginConversationForResource}
          onCreateResource={beginResourceCreation}
        />
      ) : null}

      {drawer ? <RuleAssistantDrawer
        drawer={drawer}
        ruleSetName={ruleSets.find((item) => item.id === drawer.ruleSetId)?.name}
        lexicon={lexicons.find((item) => item.id === drawer.lexiconId)}
        suggestion={drawer.payloadId ? (() => {
          const message = activeConversation.messages.find((item) => item.id === drawer.payloadId);
          return message ? getSuggestionRule(message) : undefined;
        })() : undefined}
        onClose={() => setDrawer(null)}
        onContinueSuggestion={() => setDrawer((current) => current ? { ...current, type: "candidate-editor" } : null)}
        onSaveSuggestion={(rule) => {
          if (!drawer.payloadId) return;
          const message = activeConversation.messages.find((item) => item.id === drawer.payloadId);
          setSuggestionDrafts((current) => ({ ...current, [drawer.payloadId!]: rule }));
          if (message?.status === "applied" && message.previewId) {
            updateCandidatePreviewRule(message.previewId, rule);
          }
          setDrawer(null);
          setToast(message?.status === "applied" ? "已保存调整到规则结构预览" : "候选规则已调整");
        }}
      /> : null}

      {toast ? <div className="ra-toast"><CheckCircle2 size={16} />{toast}</div> : null}
    </div>
  );
}

function ConversationRow({
  conversation,
  nested = false,
  isActive,
  isMenuOpen,
  onSelect,
  onToggleMenu,
  onMockAction
}: {
  conversation: RuleAssistantConversation;
  nested?: boolean;
  isActive: boolean;
  isMenuOpen: boolean;
  onSelect: () => void;
  onToggleMenu: () => void;
  onMockAction: (label: string) => void;
}) {
  return (
    <div className={`ra-conversation-row${nested ? " is-nested" : ""}${isActive ? " is-active" : ""}`}>
      <button type="button" className="ra-conversation-main" onClick={onSelect} title={conversation.title}>
        <span>{conversation.title}</span><time>{conversation.updatedAt}</time>
      </button>
      <button type="button" className="ra-row-more" aria-label="对话操作" onClick={onToggleMenu}><MoreHorizontal size={15} /></button>
      {isMenuOpen ? (
        <div className="ra-popover-menu ra-row-popover">
          <button type="button" onClick={() => onMockAction("重命名")}><Pencil size={13} />重命名</button>
          <button type="button" onClick={() => onMockAction("移动到其他审核规则")}><GitCompareArrows size={13} />移动到其他审核规则</button>
          <button type="button" onClick={() => onMockAction("删除")}><Trash2 size={13} />删除</button>
        </div>
      ) : null}
    </div>
  );
}

function EmptyConversation({
  resourceType,
  resourceName,
  onPrompt,
  onFile,
  onCreate
}: {
  resourceType: "blank" | "global" | "rule-set" | "lexicon" | "create-rule-set" | "create-lexicon";
  resourceName?: string;
  onPrompt: (prompt: string) => void;
  onFile: () => void;
  onCreate: () => void;
}) {
  const isCreating = resourceType === "create-rule-set" || resourceType === "create-lexicon";
  const isLexicon = resourceType === "lexicon" || resourceType === "create-lexicon";
  const prompts = isCreating || resourceType === "blank" ? [] : resourceType === "global"
    ? [
        { icon: <FolderSearch size={16} />, label: "查找私域引流资源", prompt: resourceInventoryPrompt },
        { icon: <SearchCheck size={16} />, label: "评估现有资源覆盖", prompt: resourceCoveragePrompt }
      ]
    : isLexicon
      ? [
          { icon: <SearchCheck size={16} />, label: "查看当前召回词", prompt: "当前有哪些世界杯博彩相关召回词？" },
          { icon: <Plus size={16} />, label: "补充赛事召回词", prompt: "补充世界杯博彩相关召回词。" }
        ]
    : [
        { icon: <SearchCheck size={16} />, label: "1. 查看风险类别", prompt: categoryCountPrompt },
        { icon: <ShieldCheck size={16} />, label: "2. 查看高风险规则", prompt: highRiskPrompt },
        { icon: <Plus size={16} />, label: "3. 新增风险规则", prompt: addRulePrompt }
      ];

  const title = resourceType === "blank" ? "新对话"
    : resourceType === "create-rule-set" ? "创建审核规则"
    : resourceType === "create-lexicon" ? "创建黑话库"
      : resourceType === "global" ? "知识资源综合问答" : resourceName;
  const description = resourceType === "blank" ? null
    : resourceType === "create-rule-set"
    ? "你可以描述需要建立的研判规则，也可以上传一份审核规范，由知识助手整理成完整规则结构。"
    : resourceType === "create-lexicon"
      ? "你可以描述需要召回的内容范围，也可以上传一份词表文件，由知识助手整理成词库结构。"
      : resourceType === "global"
        ? "你可以查询、比较全部审核规则和黑话库，也可以检查重复、冲突与覆盖情况。"
        : isLexicon
          ? "你可以询问当前词条、补充表达变体，或上传文件整理词库结构。"
          : "你可以询问当前规则、比较其他审核规则，或上传文件整理完整规则结构。";

  return (
    <div className="ra-empty-state">
      <div className="ra-empty-icon"><Sparkles size={28} /></div>
      <h2>{title}</h2>
      {description ? <p>{description}</p> : null}
      {resourceType !== "blank" ? <div className="ra-quick-actions">
        {prompts.map((item) => <button type="button" key={item.label} onClick={() => onPrompt(item.prompt)}>{item.icon}<span>{item.label}</span><ChevronRight size={15} /></button>)}
        <button type="button" onClick={onFile}><Paperclip size={16} /><span>{resourceType === "create-rule-set" ? "上传规则文件" : resourceType === "create-lexicon" ? "上传词表文件" : resourceType === "global" ? "导入综合资源文件" : isLexicon ? "导入词表文件" : "4. 导入规则文件"}</span><ChevronRight size={15} /></button>
        {isCreating ? (
          <>
            <button type="button" onClick={() => onPrompt(isLexicon ? "参考现有黑话库，帮我整理一份新的黑话库。" : "参考现有审核规则，帮我整理一份新的审核规则。")}>{isLexicon ? <Tags size={16} /> : <BookOpenText size={16} />}<span>{isLexicon ? "参考现有黑话库" : "参考现有审核规则"}</span><ChevronRight size={15} /></button>
            <button type="button" onClick={onCreate}><Plus size={16} /><span>从空白开始</span><ChevronRight size={15} /></button>
          </>
        ) : null}
      </div> : null}
    </div>
  );
}

function MockAttachmentPicker({
  files,
  onClose,
  onSelect
}: {
  files: MockAttachment[];
  onClose: () => void;
  onSelect: (fileName: string) => void;
}) {
  return (
    <div
      id="mock-attachment-picker"
      className="ra-mock-attachment-picker"
      role="menu"
      aria-label="选择附件文件"
      onKeyDown={(event) => {
        const items = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>("[role='menuitem']"));
        const currentIndex = items.indexOf(document.activeElement as HTMLButtonElement);
        if (event.key === "Escape") {
          event.preventDefault();
          onClose();
        } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          const delta = event.key === "ArrowDown" ? 1 : -1;
          const nextIndex = currentIndex < 0
            ? (delta > 0 ? 0 : items.length - 1)
            : (currentIndex + delta + items.length) % items.length;
          items[nextIndex]?.focus();
        }
      }}
    >
      <header><strong>选择文件</strong><button type="button" aria-label="关闭文件选择" onClick={onClose}><X size={15} /></button></header>
      <div className="ra-mock-attachment-list">
        {files.map((file) => (
          <button type="button" role="menuitem" key={file.name} onClick={() => onSelect(file.name)}>
            <span className={`ra-mock-file-icon is-${file.fileType.toLowerCase()}`}><FileText size={17} /></span>
            <span><strong>{file.name}</strong><small>{file.description}</small></span>
            <em>{file.fileType}</em>
          </button>
        ))}
      </div>
    </div>
  );
}

function ResourceMentionPicker({
  ruleSets,
  lexicons,
  onClose,
  onSelect
}: {
  ruleSets: RuleAssistantRuleSet[];
  lexicons: RuleAssistantLexicon[];
  onClose: () => void;
  onSelect: (resource: WorkspaceResourceSelection) => void;
}) {
  const resources = [
    ...ruleSets.map((resource) => ({ type: "rule-set" as const, resource })),
    ...lexicons.map((resource) => ({ type: "lexicon" as const, resource }))
  ];
  const recentResources = resources.filter(({ resource }) => (
    resource.id === "ruleset-erotic" || resource.id === "recall-gambling"
  ));
  const renderResources = (
    items: Array<{ type: "rule-set" | "lexicon"; resource: RuleAssistantRuleSet | RuleAssistantLexicon }>,
    section: string
  ) => items.map(({ type, resource }) => (
    <button type="button" role="menuitem" key={`${section}-${type}-${resource.id}`} onClick={() => onSelect({ type, id: resource.id })}>
      {type === "rule-set" ? <BookOpenText size={15} /> : <Tags size={15} />}
      <span>{resource.name}</span>
    </button>
  ));

  return (
    <div
      id="resource-mention-picker"
      className="ra-resource-mention-picker"
      role="menu"
      aria-label="选择工作资源"
      onKeyDown={(event) => {
        const items = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>("[role='menuitem']"));
        const currentIndex = items.indexOf(document.activeElement as HTMLButtonElement);
        if (event.key === "Escape") {
          event.preventDefault();
          onClose();
        } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          const delta = event.key === "ArrowDown" ? 1 : -1;
          const nextIndex = currentIndex < 0
            ? (delta > 0 ? 0 : items.length - 1)
            : (currentIndex + delta + items.length) % items.length;
          items[nextIndex]?.focus();
        } else if (event.key === "Home" || event.key === "End") {
          event.preventDefault();
          items[event.key === "Home" ? 0 : items.length - 1]?.focus();
        }
      }}
    >
      <header><strong>选择工作资源</strong><button type="button" aria-label="关闭资源选择" onClick={onClose}><X size={15} /></button></header>
      <section><h3>最近使用</h3>{renderResources(recentResources, "recent")}</section>
      <section><h3>审核规则</h3>{renderResources(resources.filter((item) => item.type === "rule-set"), "rule-set")}</section>
      <section><h3>黑话库</h3>{renderResources(resources.filter((item) => item.type === "lexicon"), "lexicon")}</section>
    </div>
  );
}

function NewWorkModal({
  view,
  ruleSets,
  lexicons,
  selectedResource,
  selectedResourceType,
  onClose,
  onViewChange,
  onSelectResource,
  onSelectResourceType,
  onCreateGlobal,
  onCreateConversation,
  onCreateResource
}: {
  view: "start" | "resource" | "resource-type";
  ruleSets: RuleAssistantRuleSet[];
  lexicons: RuleAssistantLexicon[];
  selectedResource: { type: "rule-set" | "lexicon"; id: string } | null;
  selectedResourceType: "rule-set" | "lexicon";
  onClose: () => void;
  onViewChange: (view: "start" | "resource" | "resource-type") => void;
  onSelectResource: (resource: { type: "rule-set" | "lexicon"; id: string }) => void;
  onSelectResourceType: (type: "rule-set" | "lexicon") => void;
  onCreateGlobal: () => void;
  onCreateConversation: () => void;
  onCreateResource: () => void;
}) {
  const title = view === "start" ? "开始一项新工作" : view === "resource" ? "选择工作资源" : "创建什么资源？";
  return (
    <div className="ra-modal-backdrop" onMouseDown={onClose}>
      <section className={`ra-modal ra-new-work-modal is-${view}`} role="dialog" aria-modal="true" aria-labelledby="ra-new-work-title" onMouseDown={(event) => event.stopPropagation()}>
        <div className="ra-modal-head">
          <div><h2 id="ra-new-work-title">{title}</h2>{view === "start" ? <p>所有新工作都从这里开始</p> : null}</div>
          <button type="button" onClick={onClose} aria-label="关闭"><X size={18} /></button>
        </div>

        {view === "start" ? (
          <div className="ra-new-work-options">
            <button type="button" onClick={onCreateGlobal}><Bot size={18} /><div><strong>综合问答</strong><span>查询、比较多个审核规则和黑话库</span></div><ChevronRight size={16} /></button>
            <button type="button" onClick={() => onViewChange("resource")}><FolderSearch size={18} /><div><strong>基于已有资源继续</strong><span>围绕现有审核规则或黑话库开展工作</span></div><ChevronRight size={16} /></button>
            <button type="button" onClick={() => onViewChange("resource-type")}><Plus size={18} /><div><strong>创建新资源</strong><span>通过对话创建新的审核规则或黑话库</span></div><ChevronRight size={16} /></button>
          </div>
        ) : null}

        {view === "resource" ? (
          <div className="ra-resource-picker">
            <ResourceRadioGroup title="审核规则" type="rule-set" items={ruleSets} selectedResource={selectedResource} onSelect={onSelectResource} />
            <ResourceRadioGroup title="黑话库" type="lexicon" items={lexicons} selectedResource={selectedResource} onSelect={onSelectResource} />
          </div>
        ) : null}

        {view === "resource-type" ? (
          <div className="ra-resource-type-picker">
            <label className={selectedResourceType === "rule-set" ? "is-selected" : ""}><input type="radio" name="resource-type" checked={selectedResourceType === "rule-set"} onChange={() => onSelectResourceType("rule-set")} /><BookOpenText size={18} /><div><strong>审核规则</strong><span>管理风险分类、判断条件、豁免条件和研判要求</span></div></label>
            <label className={selectedResourceType === "lexicon" ? "is-selected" : ""}><input type="radio" name="resource-type" checked={selectedResourceType === "lexicon"} onChange={() => onSelectResourceType("lexicon")} /><Tags size={18} /><div><strong>黑话库</strong><span>管理搜索词、表达变体</span></div></label>
          </div>
        ) : null}

        {view !== "start" ? (
          <div className="ra-modal-actions">
            <button type="button" onClick={() => view === "resource" ? onClose() : onViewChange("start")}>{view === "resource" ? "取消" : "返回"}</button>
            <button type="button" className="is-primary" disabled={view === "resource" && !selectedResource} onClick={view === "resource" ? onCreateConversation : onCreateResource}>{view === "resource" ? "开始对话" : "开始创建"}</button>
          </div>
        ) : null}
      </section>
    </div>
  );
}

function ResourceRadioGroup({
  title,
  type,
  items,
  selectedResource,
  onSelect
}: {
  title: string;
  type: "rule-set" | "lexicon";
  items: Array<RuleAssistantRuleSet | RuleAssistantLexicon>;
  selectedResource: { type: "rule-set" | "lexicon"; id: string } | null;
  onSelect: (resource: { type: "rule-set" | "lexicon"; id: string }) => void;
}) {
  return (
    <fieldset>
      <legend>{title}</legend>
      {items.map((item) => (
        <label key={item.id} className={selectedResource?.type === type && selectedResource.id === item.id ? "is-selected" : ""}>
          <input type="radio" name="work-resource" checked={selectedResource?.type === type && selectedResource.id === item.id} onChange={() => onSelect({ type, id: item.id })} />
          <span>{item.name}</span>
        </label>
      ))}
    </fieldset>
  );
}

function RuleAssistantDrawer({
  drawer,
  ruleSetName,
  lexicon,
  suggestion,
  onClose,
  onContinueSuggestion,
  onSaveSuggestion
}: {
  drawer: RuleAssistantDrawerState;
  ruleSetName?: string;
  lexicon?: RuleAssistantLexicon;
  suggestion?: RiskRule;
  onClose: () => void;
  onContinueSuggestion: () => void;
  onSaveSuggestion: (rule: RiskRule) => void;
}) {
  const title = drawer.type === "rule" ? "规则证据"
    : drawer.type === "lexicon" ? "词库证据"
      : drawer.type === "candidate-editor" ? "调整候选规则"
        : "规则修改内容";
  return (
    <aside className="ra-drawer" aria-label={title}>
      <header><div><span>{drawer.type === "candidate-editor" ? "候选规则" : "规则助手详情"}</span><h2>{title}</h2></div><button type="button" onClick={onClose} aria-label="关闭详情"><X size={19} /></button></header>
      <div className="ra-drawer-body">
        {drawer.type === "rule" ? (
          drawer.ruleSetId === "ruleset-gambling" ? <>
              <div className="ra-drawer-rule-head"><span>所属审核规则</span><strong>{ruleSetName || "赌博博彩审核规则"}</strong></div>
              <section><h3>明确展示下注入口或二维码</h3><p>直接展示非法外部赌博网站、App 下载二维码或第三方联系方式。</p></section>
              <section><h3>评论区诱导外部私聊</h3><p>使用“看主页联系方式”“带单稳赢私”等表达引导用户离开平台。</p></section>
              <div className="ra-drawer-note"><CircleAlert size={15} /><p>两条规则均依赖赌博或盘口语境，不能替代跨场景的通用私域引流规则。</p></div>
            </> : <>
              <div className="ra-drawer-rule-head"><span>所属审核规则</span><strong>{ruleSetName || "色情低俗审核规则"}</strong></div>
              <section><h3>局部露拍</h3><p>镜头刻意聚焦胸部、臀部、裆部或大腿根等敏感部位，并明显弱化人物整体、服装或场景。</p></section>
              <section><h3>建议风险等级</h3><p>中风险</p></section>
              <section><h3>豁免条件</h3><p>正常体育运动、舞蹈表演、健身赛事或服装展示中短暂出现的合理镜头。</p></section>
              <section><h3>检测能力</h3><div className="ra-drawer-tags"><span>图片证据提取</span><span>视频关键帧提取</span><span>融合研判</span></div></section>
              <div className="ra-drawer-note"><CircleAlert size={15} /><p>仅出现相关身体部位不构成命中，需结合镜头聚焦和展示意图判断。</p></div>
            </>
        ) : null}
        {drawer.type === "lexicon" ? (
          <>
            <div className="ra-drawer-rule-head"><span>黑话库</span><strong>{lexicon?.name || "黑话库"}</strong></div>
            <section><h3>使用范围</h3><p>{lexicon?.description || "用于特定风险场景下的内容检索与召回。"}</p></section>
            <section>
              <h3>相关证据词</h3>
              <div className="ra-drawer-tags">
                {(drawer.lexiconId === "recall-gambling"
                  ? ["外围盘口", "代理开户", "滚球下注"]
                  : ["高额返利刷单", "内幕炒股群", "日赚百元兼职"]
                ).map((term) => <span key={term}>{term}</span>)}
              </div>
            </section>
            <div className="ra-drawer-note"><CircleAlert size={15} /><p>这些词可提供场景线索，但尚未覆盖通用二维码、联系方式和平台变体表达。</p></div>
          </>
        ) : null}
        {drawer.type === "suggestion-diff" && suggestion ? (
          <>
            <div className="ra-drawer-rule-head"><span>所属风险类型</span><strong>身体隐私部位暴露 · {suggestion.name}</strong></div>
            <section className="ra-drawer-change-section">
              <h3>风险等级</h3>
              <div className="ra-before-after-row"><span>修改前</span><p>{originalLocalCloseupRule.suggestedLevel}</p></div>
              <div className="ra-before-after-row is-after"><span>修改后</span><p>{suggestion.suggestedLevel}</p></div>
            </section>
            <section className="ra-drawer-change-section">
              <h3>规则内容</h3>
              <div className="ra-before-after-row"><span>修改前</span><p>{originalLocalCloseupRule.content}</p></div>
              <div className="ra-before-after-row is-after"><span>修改后</span><p>{suggestion.content}</p></div>
            </section>
            <section>
              <h3>未调整字段</h3>
              <div className="ra-unchanged-fields">
                <div><span>豁免条件</span><p>{suggestion.exemptionConditions}</p></div>
                <div><span>应用阶段</span><p>{suggestion.applicationStages.join(" · ")}</p></div>
                {suggestion.notes ? <div><span>判断注意事项</span><p>{suggestion.notes}</p></div> : null}
              </div>
            </section>
            <div className="ra-drawer-footer-actions">
              <button type="button" onClick={onClose}>关闭</button>
              <button type="button" className="is-primary" onClick={onContinueSuggestion}>继续调整</button>
            </div>
          </>
        ) : null}
        {drawer.type === "candidate-editor" && suggestion ? (
          <CandidateRuleForm rule={suggestion} onCancel={onClose} onSave={onSaveSuggestion} />
        ) : null}
      </div>
    </aside>
  );
}

function CandidateRuleForm({
  rule,
  onCancel,
  onSave
}: {
  rule: RiskRule;
  onCancel: () => void;
  onSave: (rule: RiskRule) => void;
}) {
  const [form, setForm] = useState<RiskRule>(() => ({ ...rule, applicationStages: [...rule.applicationStages] }));
  const stages: RiskRule["applicationStages"] = ["图片证据提取", "视频关键帧提取", "融合研判"];
  const toggleStage = (stage: RiskRule["applicationStages"][number]) => {
    setForm((current) => ({
      ...current,
      applicationStages: current.applicationStages.includes(stage)
        ? current.applicationStages.filter((item) => item !== stage)
        : [...current.applicationStages, stage]
    }));
  };

  return (
    <div className="ra-candidate-form">
      <label><span>所属风险类型</span><div className="ra-readonly-field">身体隐私部位暴露</div></label>
      <label><span>规则名称</span><input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
      <label><span>规则内容</span><textarea rows={5} value={form.content} onChange={(event) => setForm({ ...form, content: event.target.value })} /></label>
      <fieldset>
        <legend>建议风险等级</legend>
        <div className="ra-level-control">
          {(["低风险", "中风险", "高风险"] as const).map((level) => (
            <button type="button" className={form.suggestedLevel === level ? "is-selected" : ""} key={level} onClick={() => setForm({ ...form, suggestedLevel: level })}>
              {form.suggestedLevel === level ? "● " : "○ "}{level}
            </button>
          ))}
        </div>
      </fieldset>
      <label><span>豁免条件</span><textarea rows={4} value={form.exemptionConditions || ""} onChange={(event) => setForm({ ...form, exemptionConditions: event.target.value })} /></label>
      <fieldset>
        <legend>应用阶段</legend>
        <div className="ra-stage-options">
          {stages.map((stage) => <label key={stage}><input type="checkbox" checked={form.applicationStages.includes(stage)} onChange={() => toggleStage(stage)} /><span>{stage}</span></label>)}
        </div>
      </fieldset>
      <label><span>判断注意事项</span><textarea rows={3} value={form.notes || ""} onChange={(event) => setForm({ ...form, notes: event.target.value })} /></label>
      <div className="ra-drawer-footer-actions">
        <button type="button" onClick={onCancel}>取消</button>
        <button type="button" className="is-primary" disabled={!form.name.trim() || !form.content.trim()} onClick={() => onSave({ ...form, name: form.name.trim(), content: form.content.trim() })}>保存调整</button>
      </div>
    </div>
  );
}
