import { createContext, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { AuditRuleSet, RiskRule } from "../../types/investigation";
import { mockAuditRuleSets } from "../../mocks/investigationMocks";
import {
  addedTransparentClothingRule,
  createEmptyLexiconConversation,
  createEmptyRuleConversation,
  createMockCandidatePreview,
  createMockConversationCandidatePreview,
  createMockLexiconSuggestionPreview,
  initialRuleAssistantConversations,
  initialRuleAssistantLexicons,
  initialRuleAssistantRuleSets,
  modifiedLocalCloseupContent
} from "./mockData";
import type {
  RuleAssistantCandidatePreview,
  RuleAssistantCandidateLexicon,
  RuleAssistantCandidateLexiconPreview,
  RuleAssistantCandidateRuleSet,
  RuleAssistantConversation,
  RuleAssistantMessage,
  RuleAssistantLexicon,
  RuleAssistantPreviewRuleChange,
  RuleAssistantReturnLocation,
  RuleAssistantRuleSet
} from "./types";

interface RuleAssistantWorkspaceValue {
  ruleSets: RuleAssistantRuleSet[];
  lexicons: RuleAssistantLexicon[];
  conversations: RuleAssistantConversation[];
  candidatePreviews: Record<string, RuleAssistantCandidatePreview>;
  candidateLexiconPreviews: Record<string, RuleAssistantCandidateLexiconPreview>;
  appliedRuleSets: Record<string, RuleAssistantCandidateRuleSet>;
  appliedLexicons: Record<string, RuleAssistantCandidateLexicon>;
  activeConversationId: string;
  expandedRuleSetIds: Set<string>;
  expandedLexiconIds: Set<string>;
  returnLocation: RuleAssistantReturnLocation | null;
  setActiveConversationId: (id: string) => void;
  setExpandedRuleSetIds: (
    updater: Set<string> | ((current: Set<string>) => Set<string>)
  ) => void;
  setExpandedLexiconIds: (
    updater: Set<string> | ((current: Set<string>) => Set<string>)
  ) => void;
  setReturnLocation: (location: RuleAssistantReturnLocation | null) => void;
  openRuleSet: (ruleSetId: string) => string;
  createConversation: (ruleSetId: string | null, firstMessage?: RuleAssistantMessage) => string;
  createLexiconConversation: (lexiconId: string, firstMessage?: RuleAssistantMessage) => string;
  createResourceConversation: (
    type: "rule-set" | "lexicon",
    conversationId?: string,
    resourceId?: string
  ) => string;
  discardDraftResource: (
    type: "rule-set" | "lexicon",
    resourceId: string,
    conversationId?: string
  ) => void;
  completeResourceCreation: (
    conversationId: string,
    type: "rule-set" | "lexicon",
    resource?: { name: string; description?: string }
  ) => string;
  createRuleSetConversation: () => string;
  updateConversation: (id: string, updater: (conversation: RuleAssistantConversation) => RuleAssistantConversation) => void;
  prepareCandidatePreview: (options: {
    conversationId: string;
    sourceRuleSetId: string | null;
    createOnly: boolean;
    fileName: string;
  }) => string;
  prepareLexiconCandidatePreview: (options: {
    conversationId: string;
    sourceLexiconId: string;
  }) => string;
  updateCandidateLexicon: (previewId: string, library: RuleAssistantCandidateLexicon) => void;
  applyLexiconCandidatePreview: (previewId: string, library?: RuleAssistantCandidateLexicon) => void;
  completeLexiconCandidatePreview: (
    previewId: string,
    status: RuleAssistantCandidateLexiconPreview["status"]
  ) => void;
  updateCandidateRuleSet: (previewId: string, ruleSet: RuleAssistantCandidateRuleSet) => void;
  updateCandidatePreviewRule: (previewId: string, rule: RiskRule) => void;
  applyConversationSuggestion: (options: {
    conversationId: string;
    sourceRuleSetId: string;
    kind: "modify" | "add";
    candidateRule?: RiskRule;
  }) => string;
  removeCandidateRule: (previewId: string, categoryId: string, ruleId: string) => void;
  applyCandidatePreview: (previewId: string, status?: "applied" | "replaced") => void;
  completeCandidatePreview: (previewId: string, status: "applied" | "replaced" | "created" | "cancelled") => void;
  addRuleSet: (name: string, description?: string, initialRuleSet?: RuleAssistantCandidateRuleSet, conversationId?: string) => string;
  upsertRuleSet: (ruleSet: AuditRuleSet) => void;
  upsertLexicon: (lexicon: RuleAssistantLexicon | RuleAssistantCandidateLexicon) => void;
}

const RuleAssistantWorkspaceContext = createContext<RuleAssistantWorkspaceValue | null>(null);

const isDraftResource = (resource: RuleAssistantRuleSet | RuleAssistantLexicon) => {
  if (resource.isDraft !== undefined) return resource.isDraft;
  return resource.id.startsWith("ruleset-draft-") || resource.id.startsWith("lexicon-draft-");
};

function withRecalculatedChanges(
  preview: RuleAssistantCandidatePreview,
  ruleSet: RuleAssistantCandidateRuleSet
): RuleAssistantCandidatePreview {
  const ruleChanges = Object.fromEntries(Object.entries(preview.ruleChanges).map(([ruleId, change]) => {
    if (change.kind !== "modified" || !change.before) return [ruleId, change];
    const after = ruleSet.categories.flatMap((category) => category.rules).find((rule) => rule.id === ruleId);
    if (!after) return [ruleId, change];
    const fields: RuleAssistantPreviewRuleChange["changedFields"] = [];
    if (after.name !== change.before.name) fields.push("name");
    if (after.content !== change.before.content) fields.push("content");
    if (after.suggestedLevel !== change.before.suggestedLevel) fields.push("suggestedLevel");
    if (after.exemptionConditions !== change.before.exemptionConditions) fields.push("exemptionConditions");
    if (after.applicationStages.join("|") !== change.before.applicationStages.join("|")) fields.push("applicationStages");
    if ((after.notes || "") !== (change.before.notes || "")) fields.push("notes");
    return [ruleId, { ...change, changedFields: fields }];
  }));
  return { ...preview, ruleSet, ruleChanges };
}

export function RuleAssistantWorkspaceProvider({ children }: { children: ReactNode }) {
  const [ruleSets, setRuleSets] = useState(initialRuleAssistantRuleSets);
  const [lexicons, setLexicons] = useState(initialRuleAssistantLexicons);
  const [conversations, setConversations] = useState(initialRuleAssistantConversations);
  const [candidatePreviews, setCandidatePreviews] = useState<Record<string, RuleAssistantCandidatePreview>>({});
  const [candidateLexiconPreviews, setCandidateLexiconPreviews] = useState<Record<string, RuleAssistantCandidateLexiconPreview>>({});
  const [appliedRuleSets, setAppliedRuleSets] = useState<Record<string, RuleAssistantCandidateRuleSet>>({});
  const [appliedLexicons, setAppliedLexicons] = useState<Record<string, RuleAssistantCandidateLexicon>>({});
  const [activeConversationId, setActiveConversationId] = useState("global-private-domain");
  const [expandedRuleSetIds, setExpandedRuleSetIds] = useState<Set<string>>(
    () => new Set(["ruleset-erotic"])
  );
  const [expandedLexiconIds, setExpandedLexiconIds] = useState<Set<string>>(
    () => new Set()
  );
  const [returnLocation, setReturnLocation] = useState<RuleAssistantReturnLocation | null>(null);

  const ensureRuleSetExpanded = (ruleSetId: string | null) => {
    if (!ruleSetId) return;
    setExpandedRuleSetIds((current) => {
      if (current.has(ruleSetId)) return current;
      const next = new Set(current);
      next.add(ruleSetId);
      return next;
    });
  };

  const createConversation = (ruleSetId: string | null, firstMessage?: RuleAssistantMessage) => {
    const conversation = createEmptyRuleConversation(ruleSetId, conversations.length + 1);
    if (firstMessage) {
      conversation.messages = [firstMessage];
      conversation.title = firstMessage.content.slice(0, 18);
    }
    setConversations((current) => [conversation, ...current]);
    setActiveConversationId(conversation.id);
    ensureRuleSetExpanded(ruleSetId);
    return conversation.id;
  };

  const createLexiconConversation = (lexiconId: string, firstMessage?: RuleAssistantMessage) => {
    const conversation = createEmptyLexiconConversation(lexiconId, conversations.length + 1);
    if (firstMessage) {
      conversation.messages = [firstMessage];
      conversation.title = firstMessage.content.slice(0, 18);
    }
    setConversations((current) => [conversation, ...current]);
    setActiveConversationId(conversation.id);
    setExpandedRuleSetIds(new Set());
    setExpandedLexiconIds(new Set([lexiconId]));
    return conversation.id;
  };

  const openRuleSet = (ruleSetId: string) => {
    const recent = conversations.find((conversation) => conversation.ruleSetId === ruleSetId);
    ensureRuleSetExpanded(ruleSetId);
    if (recent) {
      setActiveConversationId(recent.id);
      return recent.id;
    }
    return createConversation(ruleSetId);
  };

  const createRuleSetConversation = () => {
    const conversation = createEmptyRuleConversation(null, conversations.length + 1);
    conversation.title = "创建新审核规则";
    conversation.purpose = "create-rule-set";
    setConversations((current) => [conversation, ...current]);
    setActiveConversationId(conversation.id);
    return conversation.id;
  };

  const createResourceConversation = (
    type: "rule-set" | "lexicon",
    conversationId?: string,
    requestedResourceId?: string
  ) => {
    const resourceId = requestedResourceId
      || `${type === "rule-set" ? "ruleset" : "lexicon"}-draft-${Date.now()}`;
    const previousConversation = conversationId
      ? conversations.find((conversation) => conversation.id === conversationId)
      : undefined;
    const createdConversation = conversationId
      ? null
      : createEmptyRuleConversation(null, conversations.length + 1);
    const targetConversationId = conversationId || createdConversation!.id;
    const purpose = type === "rule-set" ? "create-rule-set" : "create-lexicon";
    const scenario = type === "rule-set" ? "create-terror-rule-set" : "create-terror-lexicon";
    const title = type === "rule-set" ? "创建审核规则" : "创建黑话库";
    const bindForCreation = (conversation: RuleAssistantConversation): RuleAssistantConversation => ({
      ...conversation,
      title,
      ruleSetId: type === "rule-set" ? resourceId : null,
      lexiconId: type === "lexicon" ? resourceId : null,
      purpose,
      scenario,
      updatedAt: "刚刚"
    });
    if (type === "rule-set") {
      setRuleSets((current) => [
        { id: resourceId, name: "新建审核规则", description: "正在通过对话创建", isDraft: true },
        ...current.filter((item) => (
          item.id !== resourceId
          && !(item.id === previousConversation?.ruleSetId && isDraftResource(item))
        ))
      ]);
      setLexicons((current) => current.filter((item) => (
        !(item.id === previousConversation?.lexiconId && isDraftResource(item))
      )));
      setExpandedRuleSetIds(new Set([resourceId]));
      setExpandedLexiconIds(new Set());
    } else {
      setLexicons((current) => [
        { id: resourceId, name: "新建黑话库", description: "正在通过对话创建", isDraft: true },
        ...current.filter((item) => (
          item.id !== resourceId
          && !(item.id === previousConversation?.lexiconId && isDraftResource(item))
        ))
      ]);
      setRuleSets((current) => current.filter((item) => (
        !(item.id === previousConversation?.ruleSetId && isDraftResource(item))
      )));
      setExpandedRuleSetIds(new Set());
      setExpandedLexiconIds(new Set([resourceId]));
    }
    setConversations((current) => conversationId
      ? current.map((conversation) => conversation.id === conversationId ? bindForCreation(conversation) : conversation)
      : [bindForCreation(createdConversation!), ...current]);
    setActiveConversationId(targetConversationId);
    return targetConversationId;
  };

  const discardDraftResource = (
    type: "rule-set" | "lexicon",
    resourceId: string,
    conversationId?: string
  ) => {
    if (type === "rule-set") {
      setRuleSets((current) => current.filter((item) => (
        item.id !== resourceId || !isDraftResource(item)
      )));
      setExpandedRuleSetIds((current) => {
        if (!current.has(resourceId)) return current;
        const next = new Set(current);
        next.delete(resourceId);
        return next;
      });
    } else {
      setLexicons((current) => current.filter((item) => (
        item.id !== resourceId || !isDraftResource(item)
      )));
      setExpandedLexiconIds((current) => {
        if (!current.has(resourceId)) return current;
        const next = new Set(current);
        next.delete(resourceId);
        return next;
      });
    }
    if (conversationId) {
      setConversations((current) => current.map((conversation) => {
        if (conversation.id !== conversationId) return conversation;
        const isBound = type === "rule-set"
          ? conversation.ruleSetId === resourceId
          : conversation.lexiconId === resourceId;
        if (!isBound) return conversation;
        return {
          ...conversation,
          ruleSetId: type === "rule-set" ? null : conversation.ruleSetId,
          lexiconId: type === "lexicon" ? null : conversation.lexiconId,
          purpose: "standard",
          scenario: undefined,
          updatedAt: "刚刚"
        };
      }));
    }
  };

  const completeResourceCreation = (
    conversationId: string,
    type: "rule-set" | "lexicon",
    resource?: { name: string; description?: string }
  ) => {
    const creationConversation = conversations.find((conversation) => conversation.id === conversationId);
    const existingResourceId = type === "rule-set" ? creationConversation?.ruleSetId : creationConversation?.lexiconId;
    const resourceId = existingResourceId || `${type === "rule-set" ? "ruleset" : "lexicon"}-created-${Date.now()}`;
    if (type === "rule-set") {
      const name = resource?.name || "暴恐审核规则";
      const description = resource?.description || "暴恐与公共安全场景的风险分类与判断条件";
      setRuleSets((current) => current.some((item) => item.id === resourceId)
        ? current.map((item) => item.id === resourceId ? { ...item, name, description, isDraft: false } : item)
        : [{ id: resourceId, name, description }, ...current]);
      setAppliedRuleSets((current) => ({
        ...current,
        [resourceId]: {
          id: resourceId,
          name,
          description,
          category: "暴恐与公共安全",
          version: "",
          status: "草稿",
          updatedAt: "刚刚",
          referencedTaskCount: 0,
          generalExemptions: [],
          categories: []
        }
      }));
      setExpandedRuleSetIds(new Set([resourceId]));
      setExpandedLexiconIds(new Set());
      setConversations((current) => current.map((conversation) => conversation.id === conversationId ? {
        ...conversation,
        title: "从审核规范创建审核规则",
        ruleSetId: resourceId,
        lexiconId: null,
        purpose: "standard",
        updatedAt: "刚刚"
      } : conversation));
    } else {
      const name = resource?.name || "暴恐涉敏黑话库";
      const description = resource?.description || "暴恐组织、极端主义口号、袭击威胁及符号变体词";
      setLexicons((current) => current.some((item) => item.id === resourceId)
        ? current.map((item) => item.id === resourceId ? { ...item, name, description, isDraft: false } : item)
        : [{ id: resourceId, name, description }, ...current]);
      setExpandedRuleSetIds(new Set());
      setExpandedLexiconIds(new Set([resourceId]));
      setConversations((current) => current.map((conversation) => conversation.id === conversationId ? {
        ...conversation,
        title: "从词表创建黑话库",
        ruleSetId: null,
        lexiconId: resourceId,
        purpose: "standard",
        updatedAt: "刚刚"
      } : conversation));
    }
    setActiveConversationId(conversationId);
    return resourceId;
  };

  const updateConversation = (
    id: string,
    updater: (conversation: RuleAssistantConversation) => RuleAssistantConversation
  ) => {
    setConversations((current) => current.map((conversation) => (
      conversation.id === id ? updater(conversation) : conversation
    )));
  };

  const prepareCandidatePreview = ({
    conversationId,
    sourceRuleSetId,
    createOnly,
    fileName
  }: {
    conversationId: string;
    sourceRuleSetId: string | null;
    createOnly: boolean;
    fileName: string;
  }) => {
    const previewId = `preview-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const preview = createMockCandidatePreview({
      id: previewId,
      conversationId,
      sourceRuleSetId,
      createOnly,
      fileName
    });
    setCandidatePreviews((current) => ({ ...current, [previewId]: preview }));
    return previewId;
  };

  const prepareLexiconCandidatePreview = ({
    conversationId,
    sourceLexiconId
  }: {
    conversationId: string;
    sourceLexiconId: string;
  }) => {
    const existing = Object.values(candidateLexiconPreviews).find((preview) => (
      preview.conversationId === conversationId
      && preview.sourceLexiconId === sourceLexiconId
      && preview.status === "pending"
    ));
    if (existing) return existing.id;

    const previewId = `lexicon-preview-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const preview = createMockLexiconSuggestionPreview({
      id: previewId,
      conversationId,
      sourceLexiconId,
      sourceLibrary: appliedLexicons[sourceLexiconId]
    });
    setCandidateLexiconPreviews((current) => ({ ...current, [previewId]: preview }));
    return previewId;
  };

  const updateCandidateLexicon = (previewId: string, library: RuleAssistantCandidateLexicon) => {
    setCandidateLexiconPreviews((current) => current[previewId] ? {
      ...current,
      [previewId]: {
        ...current[previewId],
        addedTermIds: library.terms
          .filter((term) => !current[previewId].baselineTermIds.includes(term.id))
          .map((term) => term.id),
        library: {
          ...library,
          terms: library.terms.map((term) => ({ ...term }))
        }
      }
    } : current);
  };

  const completeLexiconCandidatePreview = (
    previewId: string,
    status: RuleAssistantCandidateLexiconPreview["status"]
  ) => {
    setCandidateLexiconPreviews((current) => current[previewId] ? {
      ...current,
      [previewId]: { ...current[previewId], status }
    } : current);
  };

  const applyLexiconCandidatePreview = (
    previewId: string,
    library?: RuleAssistantCandidateLexicon
  ) => {
    const preview = candidateLexiconPreviews[previewId];
    if (!preview) return;
    const savedLibrary = library || preview.library;
    const normalizedLibrary: RuleAssistantCandidateLexicon = {
      ...savedLibrary,
      id: preview.sourceLexiconId,
      terms: savedLibrary.terms.map((term) => ({ ...term }))
    };
    setAppliedLexicons((current) => ({
      ...current,
      [preview.sourceLexiconId]: normalizedLibrary
    }));
    setLexicons((current) => current.map((lexicon) => lexicon.id === preview.sourceLexiconId ? {
      ...lexicon,
      name: normalizedLibrary.name,
      description: normalizedLibrary.description
    } : lexicon));
    setCandidateLexiconPreviews((current) => current[previewId] ? {
      ...current,
      [previewId]: {
        ...current[previewId],
        status: "applied",
        library: normalizedLibrary
      }
    } : current);
  };

  const updateCandidateRuleSet = (previewId: string, ruleSet: RuleAssistantCandidateRuleSet) => {
    setCandidatePreviews((current) => {
      const preview = current[previewId];
      if (!preview) return current;
      return {
        ...current,
        [previewId]: withRecalculatedChanges(preview, ruleSet)
      };
    });
  };

  const updateCandidatePreviewRule = (previewId: string, rule: RiskRule) => {
    setCandidatePreviews((current) => {
      const preview = current[previewId];
      if (!preview) return current;
      const ruleSet: RuleAssistantCandidateRuleSet = {
        ...preview.ruleSet,
        categories: preview.ruleSet.categories.map((category) => ({
          ...category,
          rules: category.rules.map((item) => item.id === rule.id ? {
            ...rule,
            applicationStages: [...rule.applicationStages]
          } : item)
        }))
      };
      return {
        ...current,
        [previewId]: withRecalculatedChanges(preview, ruleSet)
      };
    });
  };

  const applyConversationSuggestion = ({
    conversationId,
    sourceRuleSetId,
    kind,
    candidateRule
  }: {
    conversationId: string;
    sourceRuleSetId: string;
    kind: "modify" | "add";
    candidateRule?: RiskRule;
  }) => {
    const pendingPreviews = Object.values(candidatePreviews).filter((item) => (
      item.conversationId === conversationId && item.status === "pending"
    ));
    const existing = pendingPreviews[pendingPreviews.length - 1];
    const previewId = existing?.id || `preview-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const freshPreview = existing || createMockConversationCandidatePreview({
      id: previewId,
      conversationId,
      sourceRuleSetId,
      sourceRuleSet: appliedRuleSets[sourceRuleSetId]
    });

    setCandidatePreviews((current) => {
      const preview = current[previewId] || freshPreview;
      const categories = preview.ruleSet.categories.map((category) => {
        if (kind === "add") {
          if (category.name !== "身体隐私部位暴露") return category;
          const alreadyAdded = category.rules.some((rule) => rule.id === addedTransparentClothingRule.id);
          return alreadyAdded ? category : {
            ...category,
            rules: [...category.rules, {
              ...(candidateRule || addedTransparentClothingRule),
              id: addedTransparentClothingRule.id,
              applicationStages: [...(candidateRule || addedTransparentClothingRule).applicationStages]
            }]
          };
        }

        return {
          ...category,
          rules: category.rules.map((rule) => {
            if (!(rule.id === "rule-ero-2" || /局部.*(露拍|聚焦)/.test(rule.name))) return rule;
            const nextRule: RiskRule = candidateRule ? {
              ...candidateRule,
              applicationStages: [...candidateRule.applicationStages]
            } : {
                ...rule,
                name: "局部露拍",
                suggestedLevel: "高风险",
                content: modifiedLocalCloseupContent
              };
            return {
              ...nextRule,
              id: rule.id,
              applicationStages: [...nextRule.applicationStages]
            };
          })
        };
      });

      let targetRuleId = addedTransparentClothingRule.id;
      let nextChanges = preview.ruleChanges;
      if (kind === "modify") {
        const beforeRule = preview.ruleSet.categories
          .flatMap((category) => category.rules)
          .find((rule) => rule.id === "rule-ero-2" || /局部.*(露拍|聚焦)/.test(rule.name));
        if (!beforeRule) return current;
        targetRuleId = beforeRule.id;
        nextChanges = {
          ...preview.ruleChanges,
          [targetRuleId]: {
            kind: "modified",
            changedFields: ["content", "suggestedLevel"],
            before: preview.ruleChanges[targetRuleId]?.before || { ...beforeRule, applicationStages: [...beforeRule.applicationStages] }
          }
        };
      } else {
        nextChanges = {
          ...preview.ruleChanges,
          [targetRuleId]: {
            kind: "added",
            changedFields: ["name", "content", "suggestedLevel", "exemptionConditions", "applicationStages", "notes"]
          }
        };
      }

      return {
        ...current,
        [previewId]: {
          ...preview,
          ruleSet: { ...preview.ruleSet, categories },
          ruleChanges: nextChanges
        }
      };
    });
    return previewId;
  };

  const removeCandidateRule = (previewId: string, categoryId: string, ruleId: string) => {
    setCandidatePreviews((current) => {
      const preview = current[previewId];
      if (!preview) return current;
      const { [ruleId]: _removed, ...remainingChanges } = preview.ruleChanges;
      return {
        ...current,
        [previewId]: {
          ...preview,
          ruleSet: {
            ...preview.ruleSet,
            categories: preview.ruleSet.categories.map((category) => category.id === categoryId ? {
              ...category,
              rules: category.rules.filter((rule) => rule.id !== ruleId)
            } : category)
          },
          ruleChanges: remainingChanges
        }
      };
    });
  };

  const completeCandidatePreview = (previewId: string, status: "applied" | "replaced" | "created" | "cancelled") => {
    setCandidatePreviews((current) => current[previewId] ? {
      ...current,
      [previewId]: { ...current[previewId], status }
    } : current);
  };

  const applyCandidatePreview = (previewId: string, status: "applied" | "replaced" = "applied") => {
    const preview = candidatePreviews[previewId];
    if (!preview?.sourceRuleSetId) return;
    const sourceRuleSetId = preview.sourceRuleSetId;
    const sourceSummary = ruleSets.find((ruleSet) => ruleSet.id === sourceRuleSetId);
    const originalRuleSet = mockAuditRuleSets.find((ruleSet) => ruleSet.id === sourceRuleSetId);
    setAppliedRuleSets((current) => ({
      ...current,
      [sourceRuleSetId]: {
        ...preview.ruleSet,
        id: sourceRuleSetId,
        name: sourceSummary?.name || originalRuleSet?.name || preview.ruleSet.name,
        category: originalRuleSet?.category || preview.ruleSet.category,
        version: originalRuleSet?.version || preview.ruleSet.version,
        status: originalRuleSet?.status || preview.ruleSet.status,
        referencedTaskCount: originalRuleSet?.referencedTaskCount || preview.ruleSet.referencedTaskCount,
        categories: preview.ruleSet.categories.map((category) => ({
          ...category,
          rules: category.rules.map((rule) => ({
            ...rule,
            applicationStages: [...rule.applicationStages]
          }))
        }))
      }
    }));
    completeCandidatePreview(previewId, status);
  };

  const addRuleSet = (name: string, description?: string, initialRuleSet?: RuleAssistantCandidateRuleSet, conversationId?: string) => {
    const creationConversation = conversationId
      ? conversations.find((conversation) => conversation.id === conversationId && conversation.purpose === "create-rule-set")
      : undefined;
    const ruleSetId = creationConversation?.ruleSetId || `ruleset-created-${Date.now()}`;
    const ruleSet: RuleAssistantRuleSet = {
      id: ruleSetId,
      name,
      description
    };
    setRuleSets((current) => current.some((item) => item.id === ruleSetId)
      ? current.map((item) => item.id === ruleSetId ? ruleSet : item)
      : [ruleSet, ...current]);
    if (initialRuleSet) {
      setAppliedRuleSets((current) => ({
        ...current,
        [ruleSetId]: {
          ...initialRuleSet,
          id: ruleSetId,
          name,
          description: description || initialRuleSet.description,
          categories: initialRuleSet.categories.map((category) => ({
            ...category,
            rules: category.rules.map((rule) => ({
              ...rule,
              applicationStages: [...rule.applicationStages]
            }))
          }))
        }
      }));
    }
    setExpandedRuleSetIds(new Set([ruleSet.id]));
    setExpandedLexiconIds(new Set());
    if (creationConversation) {
      setConversations((current) => current.map((conversation) => conversation.id === creationConversation.id ? {
        ...conversation,
        title: "从审核规范创建审核规则",
        purpose: "standard",
        updatedAt: "刚刚"
      } : conversation));
      setActiveConversationId(creationConversation.id);
    } else {
      createConversation(ruleSet.id);
    }
    return ruleSet.id;
  };

  const upsertRuleSet = (ruleSet: AuditRuleSet) => {
    setRuleSets((current) => {
      const summary = { id: ruleSet.id, name: ruleSet.name, description: ruleSet.category };
      return current.some((item) => item.id === ruleSet.id)
        ? current.map((item) => item.id === ruleSet.id ? summary : item)
        : [summary, ...current];
    });
    setAppliedRuleSets((current) => ({
      ...current,
      [ruleSet.id]: { ...ruleSet, description: ruleSet.category }
    }));
  };

  const upsertLexicon = (lexicon: RuleAssistantLexicon | RuleAssistantCandidateLexicon) => {
    const summary: RuleAssistantLexicon = {
      id: lexicon.id,
      name: lexicon.name,
      description: lexicon.description
    };
    setLexicons((current) => current.some((item) => item.id === lexicon.id)
      ? current.map((item) => item.id === lexicon.id ? summary : item)
      : [summary, ...current]);
    if ("terms" in lexicon) {
      setAppliedLexicons((current) => ({
        ...current,
        [lexicon.id]: {
          ...lexicon,
          terms: lexicon.terms.map((term) => ({ ...term }))
        }
      }));
    }
  };

  const value = useMemo<RuleAssistantWorkspaceValue>(() => ({
    ruleSets,
    lexicons,
    conversations,
    candidatePreviews,
    candidateLexiconPreviews,
    appliedRuleSets,
    appliedLexicons,
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
    createRuleSetConversation,
    updateConversation,
    prepareCandidatePreview,
    prepareLexiconCandidatePreview,
    updateCandidateLexicon,
    applyLexiconCandidatePreview,
    completeLexiconCandidatePreview,
    updateCandidateRuleSet,
    updateCandidatePreviewRule,
    applyConversationSuggestion,
    removeCandidateRule,
    applyCandidatePreview,
    completeCandidatePreview,
    addRuleSet,
    upsertRuleSet,
    upsertLexicon
  }), [ruleSets, lexicons, conversations, candidatePreviews, candidateLexiconPreviews, appliedRuleSets, appliedLexicons, activeConversationId, expandedRuleSetIds, expandedLexiconIds, returnLocation]);

  return (
    <RuleAssistantWorkspaceContext.Provider value={value}>
      {children}
    </RuleAssistantWorkspaceContext.Provider>
  );
}

export function useRuleAssistantWorkspace() {
  const value = useContext(RuleAssistantWorkspaceContext);
  if (!value) {
    throw new Error("useRuleAssistantWorkspace must be used inside RuleAssistantWorkspaceProvider");
  }
  return value;
}
