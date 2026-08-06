import { Navigate, useNavigate, useParams } from "react-router-dom";
import { RecallLibraryManager } from "../../pages/RecallLibraryManager";
import { createTerrorLexiconCandidate } from "./mockData";
import { useRuleAssistantWorkspace } from "./RuleAssistantWorkspaceContext";
import type { RuleAssistantCandidateLexicon } from "./types";

export function LexiconCandidatePreviewPage() {
  const navigate = useNavigate();
  const { conversationId } = useParams();
  const {
    applyLexiconCandidatePreview,
    candidateLexiconPreviews,
    completeLexiconCandidatePreview,
    conversations,
    completeResourceCreation,
    discardDraftResource,
    setActiveConversationId,
    upsertLexicon,
    updateCandidateLexicon,
    updateConversation
  } = useRuleAssistantWorkspace();
  const decodedRouteId = conversationId ? decodeURIComponent(conversationId) : "";
  const candidatePreview = candidateLexiconPreviews[decodedRouteId];
  const conversation = conversations.find((item) => item.id === candidatePreview?.conversationId)
    || conversations.find((item) => item.messages.some((message) => (
      message.kind === "lexicon-file-analysis" && message.previewId === decodedRouteId
    )))
    || conversations.find((item) => item.id === decodedRouteId);
  const fileAnalysisMessages = conversation?.messages
    .filter((message) => message.kind === "lexicon-file-analysis") || [];
  const fileAnalysisMessage = conversation?.id === decodedRouteId
    ? fileAnalysisMessages.slice(-1)[0]
    : fileAnalysisMessages.find((message) => message.previewId === decodedRouteId);
  const isCreationPreview = Boolean(
    fileAnalysisMessage
    && fileAnalysisMessage.status !== "cancelled"
    && fileAnalysisMessage.status !== "completed"
  );

  if (
    !conversation
    || (!candidatePreview && !isCreationPreview)
    || Boolean(candidatePreview && candidatePreview.status !== "pending")
  ) {
    return <Navigate to="/rule-assistant" replace />;
  }

  const candidate = candidatePreview?.library
    || fileAnalysisMessage?.lexiconDraft
    || createTerrorLexiconCandidate(`lexicon-draft-${decodedRouteId}`);
  const returnToConversation = () => {
    setActiveConversationId(conversation.id);
    navigate("/rule-assistant", { replace: true });
  };

  const abortPreview = () => {
    if (candidatePreview) {
      completeLexiconCandidatePreview(candidatePreview.id, "cancelled");
    }
    updateConversation(conversation.id, (current) => ({
      ...current,
      updatedAt: "刚刚",
      messages: current.messages.map((message) => {
        if (candidatePreview && message.previewId === candidatePreview.id) {
          return { ...message, status: "cancelled" as const };
        }
        if (!candidatePreview && message.id === fileAnalysisMessage?.id) {
          return { ...message, status: "cancelled" as const };
        }
        return message;
      })
    }));
    if (!candidatePreview) {
      const resourceId = fileAnalysisMessage?.lexiconDraft?.id
        || (conversation.purpose === "create-lexicon" ? conversation.lexiconId : null);
      if (resourceId) {
        discardDraftResource("lexicon", resourceId, conversation.id);
      }
    }
    returnToConversation();
  };

  const saveLexicon = (library: RuleAssistantCandidateLexicon) => {
    if (candidatePreview) {
      const addedCount = library.terms.filter((term) => !candidatePreview.baselineTermIds.includes(term.id)).length;
      applyLexiconCandidatePreview(candidatePreview.id, library);
      updateConversation(conversation.id, (current) => ({
        ...current,
        updatedAt: "刚刚",
        messages: [
          ...current.messages.map((message) => message.previewId === candidatePreview.id
            ? { ...message, status: "completed" as const }
            : message),
          {
            id: `lexicon-applied-${Date.now()}`,
            role: "assistant" as const,
            kind: "text" as const,
            content: `已将 ${addedCount} 个新增召回词正式添加到“${library.name}”，查询类型和表达变体已同步生效。`
          }
        ]
      }));
      returnToConversation();
      return;
    }

    const isCurrentLexiconCreation = conversation.scenario === "create-terror-lexicon"
      && Boolean(conversation.lexiconId);
    const resourceId = isCurrentLexiconCreation
      ? completeResourceCreation(conversation.id, "lexicon", {
          name: library.name,
          description: library.description
        })
      : library.id;
    upsertLexicon({
      ...library,
      id: resourceId,
      terms: library.terms.map((term) => ({ ...term }))
    });
    updateConversation(conversation.id, (current) => ({
      ...current,
      updatedAt: "刚刚",
      messages: [
        ...current.messages.map((message) => message.id === fileAnalysisMessage?.id
          ? { ...message, status: "completed" as const }
          : message),
        {
          id: `lexicon-created-${Date.now()}`,
          role: "assistant" as const,
          kind: "text" as const,
          content: `已创建召回词库“${library.name}”，当前对话已归档到该词库下。`
        }
      ]
    }));
    returnToConversation();
  };

  return (
    <main className="kc-page-root lexicon-candidate-preview-page">
      <RecallLibraryManager
        preview={{
          library: candidate,
          mode: candidatePreview ? "append" : "create",
          addedTermIds: candidatePreview?.addedTermIds,
          onCancel: returnToConversation,
          onAbort: abortPreview,
          onChange: candidatePreview
            ? (library) => updateCandidateLexicon(candidatePreview.id, library)
            : (library) => updateConversation(conversation.id, (current) => ({
                ...current,
                messages: current.messages.map((message) => message.id === fileAnalysisMessage?.id ? {
                  ...message,
                  lexiconDraft: {
                    ...library,
                    terms: library.terms.map((term) => ({ ...term }))
                  }
                } : message)
              })),
          onCreate: saveLexicon
        }}
      />
    </main>
  );
}
