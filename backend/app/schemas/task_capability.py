from typing import Any, Optional

from pydantic import BaseModel, Field


class TaskCapabilityIdentity(BaseModel):
    TaskID: str
    NodeID: str
    ItemID: str
    StagingTime: str


class TaskCapabilityFieldMapping(BaseModel):
    field: str
    path: str
    role: str
    current_value: Any = None
    mirrored_in_data_map: bool = True


class TaskCapabilityValidation(BaseModel):
    mode: str
    endpoint: str
    request_count: int
    success_response_count: int
    sends_network: bool = False
    writes_remote: bool = False
    evidence_path: Optional[str] = None


class TaskCapabilityRule(BaseModel):
    key: str
    title: str
    description: str
    values: list[str] = Field(default_factory=list)


class TaskCapabilityInputSpec(BaseModel):
    key: str
    title: str
    material_type: str
    source: str
    required: bool = True
    usage: str
    review_check: str


class TaskCapabilityOutputField(BaseModel):
    field: str
    type: str
    required: bool = True
    allowed_values: list[str] = Field(default_factory=list)
    maps_to: list[str] = Field(default_factory=list)
    description: str = ""


class TaskQuestionMaterialResource(BaseModel):
    key: str
    title: str
    material_type: str
    url: str
    required: bool = True
    purpose: str


class TaskQuestionDecisionStep(BaseModel):
    key: str
    title: str
    executor: str
    input_keys: list[str] = Field(default_factory=list)
    output_keys: list[str] = Field(default_factory=list)
    can_run_without_aidp_ui: bool = True
    status: str = "planned"


class TaskQuestionIterationCandidate(BaseModel):
    key: str
    title: str
    value: str
    risk: str


class TaskSandboxClickPlanRequest(BaseModel):
    html_url: str = ""
    html_snapshot: str = ""
    allow_remote_fetch: bool = False
    max_candidates: int = 20


class TaskSandboxClickCandidate(BaseModel):
    selector: str
    tag: str
    text: str = ""
    reason: str
    href: str = ""
    risk: str = "low"


class TaskSandboxClickPlanStep(BaseModel):
    key: str
    title: str
    status: str
    detail: str


class TaskSandboxClickPlanResponse(BaseModel):
    ok: bool
    mode: str
    sends_network: bool
    writes_remote: bool
    executes_clicks: bool
    html_url: str
    source_mode: str
    click_candidates: list[TaskSandboxClickCandidate] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)
    next_steps: list[TaskSandboxClickPlanStep] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    message: str


class TaskSandboxClickExecutionRequest(BaseModel):
    html_url: str = ""
    selectors: list[str] = Field(default_factory=list)
    allow_execute: bool = False
    allowed_domains: list[str] = Field(default_factory=list)
    max_clicks: int = 3
    timeout_ms: int = 5000


class TaskSandboxClickExecutionResult(BaseModel):
    selector: str
    status: str
    before_url: str = ""
    after_url: str = ""
    url_changed: bool = False
    dom_changed: bool = False
    popup_detected: bool = False
    animation_detected: bool = False
    interaction_detected: bool = False
    evidence: str = ""
    error: str = ""


class TaskSandboxClickInteractionSummary(BaseModel):
    has_navigation: bool = False
    has_dom_interaction: bool = False
    has_popup: bool = False
    has_animation: bool = False
    clicked_count: int = 0


class TaskSandboxClickExecutionResponse(BaseModel):
    ok: bool
    mode: str
    sends_network: bool
    writes_remote: bool
    executes_clicks: bool
    html_url: str
    allowed_domains: list[str] = Field(default_factory=list)
    click_results: list[TaskSandboxClickExecutionResult] = Field(default_factory=list)
    interaction_summary: TaskSandboxClickInteractionSummary = Field(default_factory=TaskSandboxClickInteractionSummary)
    guardrails: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    helper_endpoint: str = ""
    elapsed_ms: int = 0
    message: str


class TaskSandboxClickDraftRequest(BaseModel):
    click_results: list[TaskSandboxClickExecutionResult] = Field(default_factory=list)
    interaction_summary: TaskSandboxClickInteractionSummary = Field(default_factory=TaskSandboxClickInteractionSummary)
    web_accessible: bool = True
    remark_marker: str = "SANDBOX_CLICK_DRY_RUN"
    write_audit: bool = True


class TaskMediaInspectionPlanRequest(BaseModel):
    image_url: str = ""
    video_urls: list[str] = Field(default_factory=list)
    allow_remote_probe: bool = False


class TaskMediaResource(BaseModel):
    key: str
    title: str
    material_type: str
    url: str
    required: bool = True
    expected_output: list[str] = Field(default_factory=list)


class TaskMediaInspectionStep(BaseModel):
    key: str
    title: str
    executor: str
    input_keys: list[str] = Field(default_factory=list)
    output_keys: list[str] = Field(default_factory=list)
    status: str = "planned"
    detail: str


class TaskMediaInspectionPlanResponse(BaseModel):
    ok: bool
    mode: str
    sends_network: bool
    writes_remote: bool
    claims_visual_judgement: bool
    media_resources: list[TaskMediaResource] = Field(default_factory=list)
    inspection_steps: list[TaskMediaInspectionStep] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    message: str


class TaskMediaInspectionExecutionRequest(BaseModel):
    media_resources: list[TaskMediaResource] = Field(default_factory=list)
    allow_remote_probe: bool = False
    max_bytes: int = 65536


class TaskMediaProbeResult(BaseModel):
    key: str
    title: str
    material_type: str
    url: str
    ok: bool = False
    status_code: Optional[int] = None
    content_type: str = ""
    content_length: Optional[int] = None
    fetched_bytes: int = 0
    width: Optional[int] = None
    height: Optional[int] = None
    error: str = ""


class TaskMediaInspectionExecutionResponse(BaseModel):
    ok: bool
    mode: str
    sends_network: bool
    writes_remote: bool
    claims_visual_judgement: bool
    probe_results: list[TaskMediaProbeResult] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    message: str


class TaskVideoKeyframe(BaseModel):
    index: int = 0
    timestamp_sec: float = 0.0
    data_url: str = ""
    width: Optional[int] = None
    height: Optional[int] = None
    mime_type: str = "image/jpeg"
    artifact_path: str = ""
    preview_url: str = ""


class TaskVideoKeyframeExtractionResult(BaseModel):
    resource_key: str
    url: str
    status: str
    keyframes: list[TaskVideoKeyframe] = Field(default_factory=list)
    error: str = ""


class TaskVideoKeyframeExtractionRequest(BaseModel):
    media_resources: list[TaskMediaResource] = Field(default_factory=list)
    allow_extract: bool = False
    archive_frames: bool = False
    reuse_cached_frames: bool = False
    cache_manifest_path: str = ""
    max_frames_per_video: int = 3
    timeout_ms: int = 12000


class TaskVideoKeyframeExtractionResponse(BaseModel):
    ok: bool
    mode: str
    sends_network: bool
    writes_remote: bool
    claims_visual_judgement: bool
    helper_endpoint: str = ""
    helper_mode: str = ""
    keyframe_results: list[TaskVideoKeyframeExtractionResult] = Field(default_factory=list)
    artifact_path: str = ""
    archived_frame_count: int = 0
    cache_hit: bool = False
    guardrails: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    elapsed_ms: int = 0
    message: str


class TaskMediaImageJudgement(BaseModel):
    layout_normal: bool = False
    mojibake_or_broken_layout: bool = False
    reason: str = ""


class TaskVideoKeyframeJudgement(BaseModel):
    resource_key: str
    action_visible: bool = False
    matches_sandbox_trace: bool = False
    total_frame_count: int = 0
    supporting_frame_count: int = 0
    confidence: str = "unknown"
    review_required: bool = False
    review_hint: str = ""
    keyframe_summary: str = ""
    reason: str = ""


class TaskMediaInspectionDraftRequest(BaseModel):
    image_judgement: TaskMediaImageJudgement = Field(default_factory=TaskMediaImageJudgement)
    video_keyframe_judgements: list[TaskVideoKeyframeJudgement] = Field(default_factory=list)
    remark_marker: str = "MEDIA_INSPECTION_DRY_RUN"
    write_audit: bool = True


class TaskMediaInspectionProviderRequest(BaseModel):
    media_resources: list[TaskMediaResource] = Field(default_factory=list)
    video_keyframes: list[TaskVideoKeyframeExtractionResult] = Field(default_factory=list)
    sandbox_trace: dict[str, Any] = Field(default_factory=dict)
    operator_prompt: str = ""
    use_provider: bool = True
    auto_supplement_low_confidence: bool = False
    supplement_max_frames_per_video: int = 5
    write_audit: bool = True


class TaskHttpQuestionContextResponse(BaseModel):
    ok: bool
    mode: str
    source_mode: str
    sends_network: bool
    writes_remote: bool
    task_catalog_item_id: int
    task_id: str
    task_type_name: str
    identity: TaskCapabilityIdentity
    payload_identity: dict[str, Any] = Field(default_factory=dict)
    material_resources: list[TaskQuestionMaterialResource] = Field(default_factory=list)
    current_answer_data: dict[str, Any] = Field(default_factory=dict)
    scoring_rules: list[TaskCapabilityRule] = Field(default_factory=list)
    reason_rules: list[TaskCapabilityRule] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)
    decision_pipeline: list[TaskQuestionDecisionStep] = Field(default_factory=list)
    iteration_candidates: list[TaskQuestionIterationCandidate] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    evidence_path: Optional[str] = None
    message: str


class TaskOperationProcessPlanResponse(BaseModel):
    ok: bool
    mode: str
    operation_url: str
    task_catalog_item_id: int
    task_id: str
    task_type_name: str
    source_account_user_id: str
    claims_task: bool
    sends_network: bool
    writes_remote: bool
    submits_answer: bool
    post_claim_read_step: str
    answer_write_step: str
    guardrails: list[str] = Field(default_factory=list)
    steps: list[TaskQuestionDecisionStep] = Field(default_factory=list)
    message: str


class TaskCapabilityCardResponse(BaseModel):
    task_catalog_item_id: int
    task_id: str
    task_type_key: str
    task_type_name: str
    state: str
    capability_level: str
    endpoint: str
    recording_count: int
    recording_paths: list[str]
    identity: TaskCapabilityIdentity
    field_mappings: list[TaskCapabilityFieldMapping]
    latest_validation: TaskCapabilityValidation
    supported_actions: list[str]
    ai_input_requirements: list[str]
    ai_input_materials: list[str] = Field(default_factory=list)
    ai_input_spec: list[TaskCapabilityInputSpec] = Field(default_factory=list)
    scoring_rules: list[TaskCapabilityRule] = Field(default_factory=list)
    reason_rules: list[TaskCapabilityRule] = Field(default_factory=list)
    ai_output_schema: list[TaskCapabilityOutputField] = Field(default_factory=list)
    guardrails: list[str]
    next_steps: list[str]


class TaskDraftBuildRequest(BaseModel):
    answer_data: dict[str, Any] = Field(default_factory=dict)
    remark_marker: str = ""
    execute: bool = False
    allow_draft_write: bool = False
    account_user_id: str = ""
    write_audit: bool = True


class TaskAiDraftBuildRequest(BaseModel):
    ai_output: dict[str, Any] = Field(default_factory=dict)
    remark_marker: str = ""
    execute: bool = False
    allow_draft_write: bool = False
    account_user_id: str = ""
    write_audit: bool = True


class TaskProviderDraftRequest(BaseModel):
    use_provider: bool = False
    operator_prompt: str = ""
    execute: bool = False
    allow_draft_write: bool = False
    account_user_id: str = ""
    write_audit: bool = True


class TaskDraftReviewApprovalRequest(BaseModel):
    ai_output: dict[str, Any] = Field(default_factory=dict)
    reviewer: str = "operator"
    review_note: str = ""
    write_audit: bool = True


class TaskDraftReviewItem(BaseModel):
    key: str
    title: str
    value: Any = None
    status: str = "needs_review"
    review_hint: str = ""


class TaskDraftReviewPreview(BaseModel):
    provider_status: str
    ai_output: dict[str, Any] = Field(default_factory=dict)
    mapped_answer_data: dict[str, Any] = Field(default_factory=dict)
    review_items: list[TaskDraftReviewItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_step: str = "人工复核 AI 草稿字段后，再决定是否受控暂存。"


class TaskDraftConfirmationGateStatus(BaseModel):
    key: str
    title: str
    required: bool = True
    passed: bool
    status: str
    detail: str
    next_step: str = ""


class TaskDraftConfirmationFieldDiff(BaseModel):
    field: str
    role: str
    current_value: Any = None
    next_value: Any = None
    changed: bool
    source_path: str


class TaskDraftRehearsalChecklistItem(BaseModel):
    key: str
    title: str
    status: str
    required: bool = True
    detail: str
    next_step: str


class TaskDraftConfirmationSheet(BaseModel):
    title: str
    status: str
    reviewer: str
    review_note: str = ""
    mapped_answer_data: dict[str, Any] = Field(default_factory=dict)
    field_diff: list[TaskDraftConfirmationFieldDiff] = Field(default_factory=list)
    gate_statuses: list[TaskDraftConfirmationGateStatus] = Field(default_factory=list)
    ready_for_gated_write: bool = False
    rehearsal_checklist: list[TaskDraftRehearsalChecklistItem] = Field(default_factory=list)
    required_gates: list[str] = Field(default_factory=list)
    allowed_endpoint: str
    forbidden_actions: list[str] = Field(default_factory=list)
    draft_evidence_path: str
    confirm_text: str
    next_step: str


class TaskDraftReviewApprovalResponse(BaseModel):
    ok: bool
    status: str
    sends_network: bool
    writes_remote: bool
    confirmation_sheet: TaskDraftConfirmationSheet
    message: str


class TaskDraftBuildResponse(BaseModel):
    ok: bool
    mode: str
    sends_network: bool
    writes_remote: bool
    endpoint: str
    payload_identity: TaskCapabilityIdentity
    payload_preview: dict[str, Any]
    allowed_fields: list[str]
    blockers: list[str]
    evidence_path: Optional[str] = None
    status_code: Optional[int] = None
    base_resp_status_code: Optional[int] = None
    ai_review_preview: Optional[TaskDraftReviewPreview] = None
    message: str


class TaskMediaInspectionProviderResponse(BaseModel):
    ok: bool
    mode: str
    sends_network: bool
    writes_remote: bool
    claims_visual_judgement: bool
    provider_status: str
    media_resources: list[TaskMediaResource] = Field(default_factory=list)
    image_judgement: TaskMediaImageJudgement = Field(default_factory=TaskMediaImageJudgement)
    video_keyframe_judgements: list[TaskVideoKeyframeJudgement] = Field(default_factory=list)
    draft_preview: Optional[TaskDraftBuildResponse] = None
    provider_call_count: int = 0
    provider_elapsed_ms: int = 0
    total_elapsed_ms: int = 0
    provider_input_text_chars: int = 0
    provider_input_image_count: int = 0
    provider_input_keyframe_count: int = 0
    provider_diagnostics: list[dict[str, str]] = Field(default_factory=list)
    supplement_attempted: bool = False
    supplement_status: str = ""
    supplement_keyframes: Optional[TaskVideoKeyframeExtractionResponse] = None
    initial_video_keyframe_judgements: list[TaskVideoKeyframeJudgement] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    message: str
