import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Download,
  FileSearch,
  MessageSquareText,
  Network,
  ShieldCheck,
  Users,
  X
} from "lucide-react";

type EvidenceTone = "high" | "medium" | "low" | "safe" | "clue";

interface ReportEvidenceDetail {
  id: string;
  sectionLabel: string;
  title: string;
  tone: EvidenceTone;
  riskLabel: string;
  subject: string;
  meta: string;
  summary: string;
  originalText?: string;
  translation?: string;
  context: string;
  basis: string[];
  collectionLabel: string;
  appendixLabel: string;
  appendixSection: "normal" | "comments" | "identity" | "relations" | "cases";
  appendixFocus?: string;
  facts?: Array<{ label: string; value: string }>;
  commentSamples?: Array<{
    subject: string;
    time: string;
    content: string;
    translation?: string;
    riskLabel: string;
  }>;
}

const reportEvidence: ReportEvidenceDetail[] = [
  {
    id: "normal-wedding",
    sectionLabel: "正常内容样本",
    title: "河南小伙与新疆古丽婚礼记录",
    tone: "safe",
    riskLabel: "无风险",
    subject: "石榴红缘",
    meta: "抖音 · 视频及评论 · 380 条评论",
    summary: "视频记录河南男子与新疆阿克苏女子的婚礼现场，评论区以祝福、外貌评价和地域婚俗讨论为主。",
    originalText: "河南小伙子跟新疆阿克苏古丽结婚了。",
    context: "发布内容为婚礼生活记录，未出现排斥通婚、民族贬损或异常导流表达。",
    basis: [
      "原帖叙事聚焦婚礼过程与新人生活，没有将个体关系扩展为群体评价。",
      "380 条评论未形成风险线索，整体互动为祝福和正常婚俗讨论。"
    ],
    collectionLabel: "已收录 641 条无风险内容",
    appendixLabel: "查看全部 641 条内容",
    appendixSection: "normal",
    appendixFocus: "91"
  },
  {
    id: "comment-attacks",
    sectionLabel: "评论区风险样本",
    title: "日常内容下聚集人身攻击与性羞辱评论",
    tone: "high",
    riskLabel: "高风险",
    subject: "我的心好累",
    meta: "抖音 · 评论证据 · 228 条评论中命中 8 条",
    summary: "原帖为女子日常内容，风险集中在评论区，包含非人化辱骂、外貌年龄羞辱、人身攻击和低俗性羞辱。",
    originalText: "ساراڭ ساراڭ ساراڭ ساراڭ ساراڭ ساراڭ",
    translation: "疯子、疯子、疯子、疯子、疯子、疯子。",
    context: "同一评论区另有“又丑又老”等外貌年龄羞辱，以及多条低俗性暗示评论。原帖发布者正文未表达相应风险。",
    basis: [
      "多条独立评论同时命中人身攻击、非人化辱骂、外貌羞辱与性羞辱。",
      "风险来自评论互动而非原帖主题，研判时需要区分发布者与评论者责任。"
    ],
    collectionLabel: "已收录 46 条风险评论",
    appendixLabel: "查看全部 46 条证据",
    appendixSection: "comments",
    appendixFocus: "758"
  },
  {
    id: "ethnic-stereotype",
    sectionLabel: "民族刻板印象样本",
    title: "生育话题评论将个体经历泛化至民族群体",
    tone: "medium",
    riskLabel: "中风险",
    subject: "麦热依姆古丽",
    meta: "抖音 · 维吾尔语评论及译文 · 47 条评论",
    summary: "原帖分享个人生育经历，一条评论将嫁给汉族与生育困难进行泛化关联。",
    originalText: "بۇ خەنزۇلارغا تەگكەن باشقا ئاياللارنىمۇ ئاڭلاۋاتىمەن كۆرۈۋاتىمەن ...",
    translation: "译文节选：其他嫁给汉族的女人也生不出孩子，我在网上看到、听到。",
    context: "评论从个体身体和生育经历推及其他跨民族婚姻女性，形成以民族身份解释生育结果的刻板关联。",
    basis: [
      "表达对象从具体个人扩展至“嫁给汉族的女人”群体。",
      "将民族身份与不孕进行因果式关联，超出个人经历讨论边界。"
    ],
    collectionLabel: "已收录 2 条民族与地域线索",
    appendixLabel: "查看全部 2 条证据",
    appendixSection: "identity",
    appendixFocus: "372"
  },
  {
    id: "regional-attack",
    sectionLabel: "地域攻击样本",
    title: "婚介服务视频下出现针对外来者的地域攻击",
    tone: "medium",
    riskLabel: "中风险",
    subject: "麦热依姆古丽",
    meta: "抖音 · 维吾尔语评论及译文 · 18 条评论",
    summary: "原帖介绍和田婚介登记和对象匹配服务，一条评论将发布者描述为污染城市的外来者。",
    originalText: "نېمىشقا كەلگەنسىز بۇ خوتەنگە شەھىرىنى بۇلغىغىلى كەلدىڭىزمۇ",
    translation: "你来和田干嘛？是来把这座城市搞得乌烟瘴气的吗？",
    context: "评论以发布者的地域流动身份为攻击基础，贬损其来到和田开展服务的行为。",
    basis: [
      "存在明确的排斥性地域指向。",
      "使用“污染城市、搞得乌烟瘴气”等贬损性表达攻击具体人物。"
    ],
    collectionLabel: "已收录 2 条民族与地域线索",
    appendixLabel: "查看全部 2 条证据",
    appendixSection: "identity",
    appendixFocus: "366"
  },
  {
    id: "charity-content",
    sectionLabel: "正向内容样本",
    title: "为和田暴雨受灾家庭征集资助线索",
    tone: "safe",
    riskLabel: "无风险",
    subject: "麦热依姆古丽",
    meta: "抖音 · 视频及评论 · 335 条评论",
    summary: "发布者计划资助十户困难家庭并向网友征集线索，评论区均为祝福、点赞和求助反馈。",
    context: "内容目的为灾后互助，互动未出现民族排斥、人身攻击或异常导流。",
    basis: [
      "视频主题和行动指向公益互助。",
      "335 条评论整体为正向回应，未发现风险表达。"
    ],
    collectionLabel: "已收录 641 条无风险内容",
    appendixLabel: "查看全部 641 条内容",
    appendixSection: "normal",
    appendixFocus: "362"
  },
  {
    id: "saya-cross-platform",
    sectionLabel: "低风险边界样本",
    title: "舞蹈视频画面出现跨平台账号信息",
    tone: "low",
    riskLabel: "低风险",
    subject: "萨娅",
    meta: "抖音 · OCR 画面文字 · 20 条评论",
    summary: "户外舞蹈内容本身正常，视频画面叠加快手和小红书账号信息，形成低强度跨平台引流线索。",
    originalText: "00:07 快手 @2458744520；00:09 小红书号：514089680",
    context: "评论以赞美和表情互动为主，未发现民族关系或其他内容风险。该线索与民族议题无直接关联。",
    basis: [
      "两处 OCR 结果均为其他平台账号信息。",
      "应作为跨平台账号线索单独观察，不应扩展为民族关系风险结论。"
    ],
    collectionLabel: "已收录 1 条低风险边界线索",
    appendixLabel: "查看完整证据",
    appendixSection: "cases",
    appendixFocus: "21"
  },
  {
    id: "shared-audience",
    sectionLabel: "互动关系线索",
    title: "跨对象互动更符合共同受众特征",
    tone: "clue",
    riskLabel: "关系线索",
    subject: "四个重点对象",
    meta: "评论账号交叉统计 · 7,450 个去重互动账号",
    summary: "131 个账号曾在至少两个重点对象的评论区出现，其中仅 1 个覆盖三个对象，没有账号覆盖全部四个对象。",
    context: "现有 46 条风险评论均来自单一对象评论区，没有风险评论账号跨对象出现，因此不能据此认定协同攻击或关联账号网络。",
    basis: [
      "“🌺🌹红花🌹🌺”在三个对象下共出现 56 次，是覆盖对象最多的普通互动账号。",
      "“维汉胡胡~招红娘”在麦热依姆古丽任务内高频互动，并在个人资料中指向双方关系，只能列为需核验线索。"
    ],
    collectionLabel: "已统计 131 个跨对象互动账号",
    appendixLabel: "查看完整共同互动统计",
    appendixSection: "relations",
    facts: [
      { label: "跨两个及以上对象", value: "131 个账号" },
      { label: "跨三个对象", value: "1 个账号" },
      { label: "跨全部四个对象", value: "0 个账号" },
      { label: "风险账号跨对象", value: "0 个账号" }
    ],
    commentSamples: [
      {
        subject: "我的心好累",
        time: "2026-05-22",
        content: "ئۈرۈك تۈكلۈك ئۈرۈك دەيمىز [呲牙][呲牙]",
        translation: "我们叫它毛杏 [呲牙][呲牙]",
        riskLabel: "无风险"
      },
      {
        subject: "麦热依姆古丽",
        time: "2026-02-03",
        content: "مەن تونۇيمەن ئوبدان قىز بالا ئۇ [呲牙][呲牙][呲牙][呲牙]",
        translation: "我认识她，是个好女孩 [呲牙][呲牙][呲牙][呲牙]",
        riskLabel: "无风险"
      },
      {
        subject: "麦热依姆古丽",
        time: "2026-02-03",
        content: "ئوخشايدىكەن سىلەر بىر جۈپ قوشماق لا 😊😊😊",
        translation: "你们真像一对璧人 😊😊😊",
        riskLabel: "无风险"
      },
      {
        subject: "石榴红缘",
        time: "2025-12-09",
        content: "[咖啡][咖啡][咖啡][赞][赞][赞][玫瑰][玫瑰][玫瑰]",
        riskLabel: "无风险"
      }
    ]
  }
];

const objectRows = [
  { name: "我的心好累", contents: 201, comments: "2,446", risks: 13, detail: "高 2 · 中 5 · 低 6" },
  { name: "麦热依姆古丽", contents: 304, comments: "12,133", risks: 12, detail: "高 2 · 中 2 · 低 8" },
  { name: "石榴红缘", contents: 105, comments: "1,808", risks: 0, detail: "未发现风险" },
  { name: "萨娅", contents: 57, comments: "2,344", risks: 1, detail: "低 1" }
];

const sectionNav = [
  ["report-overview", "1. 调查概况"],
  ["report-data", "2. 数据概览"],
  ["report-judgment", "3. 综合研判"],
  ["report-findings", "4. 主要调查发现"],
  ["report-subjects", "5. 重点对象分析"],
  ["report-relations", "6. 评论互动与关联线索"],
  ["report-cases", "7. 典型内容与研判案例"],
  ["report-conclusion", "8. 综合结论与建议"]
] as const;

function EvidenceLink({ evidenceId, children, onOpen }: {
  evidenceId: string;
  children: string;
  onOpen: (evidenceId: string) => void;
}) {
  return (
    <button type="button" className="ethnic-report-evidence-link" onClick={() => onOpen(evidenceId)}>
      <FileSearch size={15} />
      <span>{children}</span>
    </button>
  );
}

function ReportEvidenceDrawer({ evidence, onClose, onViewAll }: {
  evidence: ReportEvidenceDetail | null;
  onClose: () => void;
  onViewAll: (evidence: ReportEvidenceDetail) => void;
}) {
  useEffect(() => {
    if (!evidence) return;
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [evidence, onClose]);

  if (!evidence) return null;

  return createPortal(
    <div className="ethnic-evidence-layer" role="presentation">
      <button type="button" className="ethnic-evidence-backdrop" aria-label="关闭证据详情" onClick={onClose} />
      <aside className="ethnic-evidence-drawer" role="dialog" aria-modal="true" aria-labelledby="ethnic-evidence-title">
        <header className="ethnic-evidence-header">
          <div>
            <span>代表性证据</span>
            <h2 id="ethnic-evidence-title">支撑内容与研判依据</h2>
          </div>
          <button type="button" aria-label="关闭" title="关闭" onClick={onClose}>
            <X size={20} />
          </button>
        </header>

        <div className="ethnic-evidence-body">
          <div className="ethnic-evidence-collection">
            <span>{evidence.sectionLabel}</span>
            <strong>{evidence.collectionLabel}</strong>
          </div>

          <section className="ethnic-evidence-subject">
            <div className="ethnic-evidence-title-row">
              <h3>{evidence.title}</h3>
              <span className={`is-${evidence.tone}`}>{evidence.riskLabel}</span>
            </div>
            <p>{evidence.subject} · {evidence.meta}</p>
            <div>{evidence.summary}</div>
          </section>

          {evidence.facts ? (
            <section className="ethnic-evidence-facts" aria-label="关系统计">
              {evidence.facts.map((fact) => (
                <div key={fact.label}>
                  <span>{fact.label}</span>
                  <strong>{fact.value}</strong>
                </div>
              ))}
            </section>
          ) : null}

          {evidence.commentSamples ? (
            <section className="ethnic-evidence-section ethnic-evidence-comments">
              <div className="ethnic-evidence-comments-heading">
                <h4>代表性评论</h4>
                <span>展示 1 / 56 条</span>
              </div>
              {evidence.commentSamples.slice(0, 1).map((comment, index) => (
                <article key={`${comment.subject}-${comment.time}-${index}`}>
                  <header>
                    <strong>{comment.subject}</strong>
                    <span>{comment.time}</span>
                    <em>{comment.riskLabel}</em>
                  </header>
                  <p lang="ug">{comment.content}</p>
                  {comment.translation ? <p><b>译文：</b>{comment.translation}</p> : null}
                </article>
              ))}
            </section>
          ) : null}

          {evidence.originalText ? (
            <section className="ethnic-evidence-section">
              <h4>原始内容</h4>
              <blockquote lang="ug">{evidence.originalText}</blockquote>
            </section>
          ) : null}

          {evidence.translation ? (
            <section className="ethnic-evidence-section">
              <h4>中文译文</h4>
              <blockquote>{evidence.translation}</blockquote>
            </section>
          ) : null}

          <section className="ethnic-evidence-section">
            <h4>上下文</h4>
            <p>{evidence.context}</p>
          </section>

          <section className="ethnic-evidence-section">
            <h4>研判依据</h4>
            <ol>
              {evidence.basis.map((item) => <li key={item}>{item}</li>)}
            </ol>
          </section>

          <section className="ethnic-evidence-boundary">
            <ShieldCheck size={17} />
            <p>本证据仅支撑所列具体结论，不用于推断发布者整体立场或扩大至相关民族群体。</p>
          </section>
        </div>

        <footer className="ethnic-evidence-footer">
          <button type="button" onClick={() => onViewAll(evidence)}>
            <span>{evidence.appendixLabel}</span>
            <ArrowRight size={15} />
          </button>
        </footer>
      </aside>
    </div>,
    document.body
  );
}

export function InvestigationReportPage() {
  const { investigationId = "session-ethnic-relations" } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const requestedEvidenceId = searchParams.get("evidence");
  const requestedSectionId = searchParams.get("section");
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>(() => requestedEvidenceId);
  const selectedEvidence = reportEvidence.find((item) => item.id === selectedEvidenceId) || null;

  useEffect(() => {
    const previousTitle = document.title;
    document.title = "维汉民族关系专项调查报告";
    return () => {
      document.title = previousTitle;
    };
  }, []);

  useEffect(() => {
    if (requestedEvidenceId && reportEvidence.some((item) => item.id === requestedEvidenceId)) {
      setSelectedEvidenceId(requestedEvidenceId);
    }
    if (!requestedSectionId) return;
    const frameId = window.requestAnimationFrame(() => {
      document.getElementById(requestedSectionId)?.scrollIntoView({ block: "start" });
    });
    return () => window.cancelAnimationFrame(frameId);
  }, [requestedEvidenceId, requestedSectionId]);

  const openEvidence = (evidenceId: string) => setSelectedEvidenceId(evidenceId);
  const openEvidenceAppendix = (evidence: ReportEvidenceDetail) => {
    const params = new URLSearchParams({ section: evidence.appendixSection });
    if (evidence.appendixFocus) params.set("focus", evidence.appendixFocus);
    navigate(`/investigation/${encodeURIComponent(investigationId)}/report/evidence?${params.toString()}`);
  };
  const scrollToSection = (sectionId: string) => {
    document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <main className="ethnic-report-page">
      <header className="ethnic-report-toolbar">
        <button
          type="button"
          className="ethnic-report-back"
          onClick={() => navigate(`/investigation/${encodeURIComponent(investigationId)}`)}
        >
          <ArrowLeft size={17} />
          <span>返回调查会话</span>
        </button>
        <div className="ethnic-report-toolbar-title">
          <span>专项调查报告</span>
          <strong>维汉民族关系专项调查</strong>
        </div>
        <button type="button" className="ethnic-report-export" onClick={() => window.print()}>
          <Download size={16} />
          <span>导出报告</span>
        </button>
      </header>

      <div className="ethnic-report-layout">
        <nav className="ethnic-report-toc" aria-label="报告目录">
          <div>报告目录</div>
          {sectionNav.map(([id, label]) => (
            <button key={id} type="button" onClick={() => scrollToSection(id)}>{label}</button>
          ))}
        </nav>

        <article className="ethnic-report-paper">
          <header className="ethnic-report-cover">
            <div className="ethnic-report-classification">内部研判资料</div>
            <p>专项调查报告</p>
            <h1>维汉民族关系专项调查报告</h1>
            <div className="ethnic-report-title-rule" />
            <dl>
              <div><dt>调查平台</dt><dd>抖音</dd></div>
              <div><dt>重点对象</dt><dd>4 个</dd></div>
              <div><dt>研判样本</dt><dd>667 条</dd></div>
              <div><dt>报告日期</dt><dd>2026 年 7 月 29 日</dd></div>
            </dl>
          </header>

          <section className="ethnic-report-executive">
            <div className="ethnic-report-executive-label">摘要结论</div>
            <p>
              本次调查共研判四个重点对象的 667 条内容及 18,731 条评论。正常婚恋、家庭生活、情感表达和公益互助内容占明显主体；26 条风险内容主要由评论区触发，高中风险集中在人身攻击、低俗辱骂和性羞辱。现有样本中发现少量民族刻板印象、地域攻击及排斥通婚表达，但未形成跨对象协同传播证据，不宜将个别评论上升为对账号整体立场或群体关系的判断。
            </p>
          </section>

          <section id="report-overview" className="ethnic-report-section">
            <h2><span>一</span>调查概况</h2>
            <p>
              本次专项调查围绕维汉婚恋、跨民族家庭互动及相关评论争议展开，重点查看公开内容中是否存在民族刻板印象、侮辱歧视、排斥通婚、煽动对立及由评论互动衍生的人身攻击风险。
            </p>
            <table className="ethnic-report-scope-table">
              <tbody>
                <tr><th>调查对象</th><td>我的心好累、麦热依姆古丽、石榴红缘、萨娅</td></tr>
                <tr><th>内容范围</th><td>博主主页内容、视频与图文信息、评论及维吾尔语译文、已保存审核结果</td></tr>
                <tr><th>研判方案</th><td>维汉民族关系专题研判方案，结合民族意识形态风险规则与上下文豁免条件</td></tr>
                <tr><th>结论口径</th><td>以最终审核决定和风险等级为准，区分原帖发布者、评论者与互动账号</td></tr>
              </tbody>
            </table>
          </section>

          <section id="report-data" className="ethnic-report-section">
            <h2><span>二</span>数据概览</h2>
            <div className="ethnic-report-metrics">
              <div><strong>4</strong><span>重点调查对象</span></div>
              <div><strong>667</strong><span>研判内容</span></div>
              <div><strong>18,731</strong><span>评论记录</span></div>
              <div><strong>26</strong><span>风险内容</span></div>
              <div><strong>7,450</strong><span>去重互动账号</span></div>
            </div>

            <h3>2.1 分对象统计</h3>
            <div className="ethnic-report-table-wrap">
              <table className="ethnic-report-data-table">
                <thead><tr><th>重点对象</th><th>内容</th><th>评论</th><th>风险内容</th><th>风险构成</th></tr></thead>
                <tbody>
                  {objectRows.map((row) => (
                    <tr key={row.name}>
                      <td><strong>{row.name}</strong></td>
                      <td>{row.contents}</td>
                      <td>{row.comments}</td>
                      <td className={row.risks > 0 ? "has-risk" : "is-safe"}>{row.risks}</td>
                      <td>{row.detail}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot><tr><th>合计</th><th>667</th><th>18,731</th><th>26</th><th>高 4 · 中 7 · 低 15</th></tr></tfoot>
              </table>
            </div>

            <h3>2.2 风险分布</h3>
            <div className="ethnic-report-risk-chart">
              <div className="ethnic-report-risk-summary">
                <div><strong>96.1%</strong><span>无风险内容占比</span></div>
                <p>641 条内容未发现风险，风险内容占全部样本的 3.9%。</p>
              </div>
              <div className="ethnic-report-bars" aria-label="风险等级分布">
                <div><span>高风险</span><i><b style={{ width: "26.7%" }} /></i><strong>4</strong></div>
                <div><span>中风险</span><i><b style={{ width: "46.7%" }} /></i><strong>7</strong></div>
                <div><span>低风险</span><i><b style={{ width: "100%" }} /></i><strong>15</strong></div>
              </div>
            </div>
          </section>

          <section id="report-judgment" className="ethnic-report-section">
            <h2><span>三</span>综合研判</h2>
            <p>
              四个重点对象的内容主题以婚恋家庭、日常生活、情感表达和公益互助为主，未发现发布者持续、系统性煽动民族对立的内容模式。风险主要发生在评论区，且多表现为针对具体人物的粗俗攻击、性羞辱、外貌年龄羞辱和非人化辱骂。
            </p>
            <p>
              与民族关系直接相关的风险线索数量有限，主要是将跨民族婚姻与生育结果进行泛化关联、对外来者的地域攻击，以及少量排斥跨民族婚恋的表达。上述线索需要关注，但尚不足以支持“存在组织化对立传播”或“重点对象主动输出民族对立内容”的结论。
            </p>
            <div className="ethnic-report-judgment-box">
              <ShieldCheck size={21} />
              <div><strong>当前结论边界</strong><p>风险评论不等于原帖风险，共同评论账号不等于关联账号，个体负面表达不等于群体立场。</p></div>
            </div>
          </section>

          <section id="report-findings" className="ethnic-report-section">
            <h2><span>四</span>主要调查发现</h2>
            <div className="ethnic-report-finding">
              <strong>4.1 正常婚恋与家庭生活内容占绝对主体</strong>
              <p>641 条内容未发现风险，典型内容包括跨民族婚礼、领证、夫妻日常、亲友互动和公益互助。石榴红缘的 105 条内容全部为无风险。</p>
              <EvidenceLink evidenceId="normal-wedding" onOpen={openEvidence}>查看支撑该结论的内容</EvidenceLink>
            </div>
            <div className="ethnic-report-finding">
              <strong>4.2 风险主要由评论互动触发，而非原帖主题</strong>
              <p>26 条风险内容中有 25 条以评论为主要证据，共保存 46 条风险评论片段。高风险样本集中出现性羞辱、极端粗俗攻击和非人化辱骂。</p>
              <EvidenceLink evidenceId="comment-attacks" onOpen={openEvidence}>查看相关评论与上下文</EvidenceLink>
            </div>
            <div className="ethnic-report-finding">
              <strong>4.3 民族刻板印象和地域排斥为零散线索</strong>
              <p>现有样本发现一条中风险民族刻板印象评论和一条中风险地域攻击评论，另有少量低风险排斥通婚表达，未见跨对象重复传播。</p>
              <div className="ethnic-report-link-row">
                <EvidenceLink evidenceId="ethnic-stereotype" onOpen={openEvidence}>查看民族刻板印象评论</EvidenceLink>
                <EvidenceLink evidenceId="regional-attack" onOpen={openEvidence}>查看地域攻击评论</EvidenceLink>
              </div>
            </div>
            <div className="ethnic-report-finding">
              <strong>4.4 共同互动账号尚不能构成关联网络结论</strong>
              <p>131 个账号跨至少两个对象互动，但 46 条风险评论均未跨对象出现。现有数据更符合共同受众特征，未发现协同攻击链路。</p>
              <EvidenceLink evidenceId="shared-audience" onOpen={openEvidence}>查看共同评论统计</EvidenceLink>
            </div>
          </section>

          <section id="report-subjects" className="ethnic-report-section">
            <h2><span>五</span>重点对象分析</h2>
            <div className="ethnic-report-subject-list">
              <article>
                <span>01</span><div><h3>我的心好累</h3><p>共研判 201 条内容，13 条存在风险，其中高风险 2 条、中风险 5 条、低风险 6 条。内容以个人情感和生活表达为主，风险集中于评论区对人物关系、年龄和外貌的攻击。</p><EvidenceLink evidenceId="comment-attacks" onOpen={openEvidence}>查看该对象的代表性风险内容</EvidenceLink></div>
              </article>
              <article>
                <span>02</span><div><h3>麦热依姆古丽</h3><p>共研判 304 条内容，12 条存在风险，其中高风险 2 条、中风险 2 条、低风险 8 条。内容覆盖维汉家庭生活、婚介服务、个人经历和公益互助，评论量最大；风险同时包含粗俗攻击、地域攻击和民族刻板印象。</p><EvidenceLink evidenceId="charity-content" onOpen={openEvidence}>查看该对象的正常代表性内容</EvidenceLink></div>
              </article>
              <article>
                <span>03</span><div><h3>石榴红缘</h3><p>共研判 105 条内容、1,808 条评论，未发现风险。内容主要记录维汉婚恋、领证和婚礼过程，评论互动以祝福和婚俗讨论为主。</p><EvidenceLink evidenceId="normal-wedding" onOpen={openEvidence}>查看该对象的代表性内容</EvidenceLink></div>
              </article>
              <article>
                <span>04</span><div><h3>萨娅</h3><p>共研判 57 条内容，仅 1 条低风险，来自画面中的跨平台账号信息。该对象部分内容描述为哈汉夫妻日常，不应在缺少身份信息时直接归入维吾尔族博主结论。</p><EvidenceLink evidenceId="saya-cross-platform" onOpen={openEvidence}>查看低风险线索及结论边界</EvidenceLink></div>
              </article>
            </div>
          </section>

          <section id="report-relations" className="ethnic-report-section">
            <h2><span>六</span>评论互动与关联线索</h2>
            <div className="ethnic-report-relation-summary">
              <div><Users size={20} /><strong>7,450</strong><span>去重互动账号</span></div>
              <div><MessageSquareText size={20} /><strong>131</strong><span>跨对象评论账号</span></div>
              <div><Network size={20} /><strong>0</strong><span>风险账号跨对象</span></div>
            </div>
            <p>
              高频互动账号主要集中在单一对象评论区。覆盖范围最广的账号“🌺🌹红花🌹🌺”在三个对象下共出现 56 次，但其互动未命中风险。另有“维汉胡胡~招红娘”在麦热依姆古丽相关任务中发布 1 条内容并留下 187 条评论，个人资料指向双方关系，可作为需进一步核验的关系线索，不能直接认定为系统中的已确认关联账号。
            </p>
            <EvidenceLink evidenceId="shared-audience" onOpen={openEvidence}>查看共同评论记录与关系边界</EvidenceLink>
          </section>

          <section id="report-cases" className="ethnic-report-section">
            <h2><span>七</span>典型内容与研判案例</h2>
            <div className="ethnic-report-case-list">
              {[
                ["comment-attacks", "案例一", "高风险", "日常内容下的聚集性攻击", "8 条风险评论覆盖多种攻击类型，风险来自评论区。"],
                ["ethnic-stereotype", "案例二", "中风险", "生育话题中的民族刻板印象", "个体经历被泛化至跨民族婚姻女性群体。"],
                ["regional-attack", "案例三", "中风险", "婚介服务内容下的地域攻击", "评论以外来者身份贬损发布者。"],
                ["normal-wedding", "案例四", "无风险", "跨民族婚礼记录", "原帖与评论整体为正常婚庆生活分享。"],
                ["saya-cross-platform", "案例五", "低风险", "与民族议题无直接关联的引流线索", "OCR 识别到跨平台账号，需单独把握风险边界。"]
              ].map(([id, no, level, title, summary]) => (
                <article key={id}>
                  <div><span>{no}</span><i className={`is-${reportEvidence.find((item) => item.id === id)?.tone}`}>{level}</i></div>
                  <h3>{title}</h3>
                  <p>{summary}</p>
                  <EvidenceLink evidenceId={id} onOpen={openEvidence}>查看原文、译文与研判依据</EvidenceLink>
                </article>
              ))}
            </div>
          </section>

          <section id="report-conclusion" className="ethnic-report-section ethnic-report-conclusion">
            <h2><span>八</span>综合结论与建议</h2>
            <div className="ethnic-report-conclusion-box">
              <CheckCircle2 size={24} />
              <p>当前样本整体呈现正常婚恋、家庭生活与情感互动特征，暂未发现重点对象持续输出民族对立内容，也未发现跨对象协同传播风险。</p>
            </div>
            <h3>8.1 重点关注事项</h3>
            <ol>
              <li>优先复核 4 条高风险和 7 条中风险内容，重点核对维吾尔语原文、译文及评论上下文。</li>
              <li>对集中出现人身攻击和性羞辱的评论区进行持续观察，区分原帖发布者与风险评论账号。</li>
              <li>对“维汉胡胡~招红娘”等关系线索做身份核验，在确认前不进入已关联账号结论。</li>
            </ol>
            <h3>8.2 研判边界</h3>
            <p>
              本报告反映当前样本和已保存审核结果。共同互动只能说明受众交叉，不能证明账号协同；个别评论者的民族刻板印象和攻击性表达，不能代表发布者本人或相关民族群体的整体态度。萨娅样本中存在哈汉家庭表述，身份归类需以进一步核验为准。
            </p>
            <div className="ethnic-report-link-row">
              <EvidenceLink evidenceId="comment-attacks" onOpen={openEvidence}>查看重点风险评论</EvidenceLink>
              <EvidenceLink evidenceId="shared-audience" onOpen={openEvidence}>查看关联线索依据</EvidenceLink>
            </div>
          </section>

          <footer className="ethnic-report-footer">
            <span>维汉民族关系专项调查报告</span>
            <span>内部研判资料 · 第 1 版</span>
          </footer>
        </article>
      </div>

      <ReportEvidenceDrawer
        evidence={selectedEvidence}
        onClose={() => setSelectedEvidenceId(null)}
        onViewAll={openEvidenceAppendix}
      />
    </main>
  );
}
