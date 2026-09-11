from copy import deepcopy
from backend.reporting.presentation_projection import _section_projection


def test_saved_section_with_partial_citations_requires_a_unique_containing_finding():
    section = dict(section_ref="section-3-1", section_type="investigation_finding",
                   claims=[dict(audit_finding_refs=["f1"], evidence_refs=["e1"])])
    finding = dict(investigation_finding_ref="if1", title="Finding", post_memberships=[
        dict(audit_finding_ref="f1", membership_evidence_refs=["e1", "e2"]),
        dict(audit_finding_ref="f2", membership_evidence_refs=["e3"]),
    ])
    def project(findings, candidate=section):
        return _section_projection(candidate, findings=findings, posts_by_ref={},
            audits_by_ref={}, evidence_by_ref={}, standalone_items=[])["finding_binding"]
    assert project([finding])["investigation_finding_ref"] == "if1"
    assert project([finding, {**finding, "investigation_finding_ref": "if2"}]) == {"status": "unavailable"}
    altered = deepcopy(section)
    altered["claims"][0]["evidence_refs"] = ["outside"]
    assert project([finding], altered) == {"status": "unavailable"}
