'use client'

import { useState } from 'react'
import { Maximize2 } from 'lucide-react'
import { Empty, Table, Tag, TD, TH, TR } from '../ui'
import { Summary, Stat } from '../testing/TestingResult'
import DiagramViewer from './DiagramViewer'
import { cn } from '@/lib/utils'


const list = (value) => Array.isArray(value) ? value : []


function line(item) {
  if (typeof item === 'string') return item
  if (!item || typeof item !== 'object') return String(item ?? '')
  const text = item.requirement || item.description || item.criterion
    || item.risk || item.rule || item.text || item.page_name
    || item.role_name || item.name || item.title

  return text || JSON.stringify(item)
}

const idOf = (item, i) =>
  (item && typeof item === 'object'
    && (item.id || item.ref || item.module || item.category || item.area)) || `#${i + 1}`

/**
 * A numbered section of the document.
 *
 * The number is set in the accent and the title in the heading face at 800,
 * over a 2px rule — the same head the printed spec would carry. The count
 * sits hard against the right edge so a column of sections lines up.
 */
function Section({ title, children, count, n }) {
  return (
    <div className="mb-6">
      <div className="flex items-baseline gap-2.5 border-b-2 border-line2 pb-[7px]">
        {n != null && <span className="font-mono text-[11px] text-accent">{n}</span>}
        <h3 className="font-display text-[15px] font-extrabold tracking-[-.01em] text-ink">
          {title}
        </h3>
        <span className="flex-1" />
        {count != null && (
          <span className="font-mono text-[10px] text-muted2">{count}</span>
        )}
      </div>
      <div className="mt-1">{children}</div>
    </div>
  )
}

function Bullets({ items, tone }) {
  if (!list(items).length) return <p className="py-2 text-[11.5px] text-muted2">None recorded.</p>
  return (
    <ul>
      {list(items).map((item, i) => (
        <li key={i} className="grid grid-cols-[34px_1fr] gap-2.5 border-b border-line
                               py-[7px] last:border-0">
          <span className={cn('font-mono text-[10.5px]', tone || 'text-faint')}>
            {idOf(item, i)}
          </span>
          <span className="text-[12px] leading-[1.5] text-ink">{line(item)}</span>
        </li>
      ))}
    </ul>
  )
}

/** Two columns of sections, with a 30px gutter and nothing else between them. */
const Columns = ({ children }) => (
  <div className="grid gap-x-[30px] md:grid-cols-2">{children}</div>
)


export function Document({ srs }) {
  const doc = srs.document || {}
  if (!srs.have?.document) return <Empty>No SRS document was adopted for this project.</Empty>

  const summary = doc.app_summary
  const isText = typeof summary === 'string'
  const blurb = isText ? summary : (summary?.short_description || '')
  const goal = isText ? '' : (summary?.business_goal || '')
  const title = doc.document_title || 'Software Requirements Specification'
  const projectName = doc.project_name || (!isText && summary?.app_name) || 'Untitled'
  const auth = Boolean(doc.authentication_requirement?.login_required)
  const plan = doc.approved_plan || {}
  const quality = doc.requirements_quality_review || {}
  const reviewItems = list(quality.items_needing_human_review)
  const tables = list(doc.database_design?.tables)
  const relationships = list(doc.database_design?.relationships)
  const matrix = list(doc.role_access_matrix)
  const hasPlan = Object.keys(plan).length > 0
  const hasRoleMatrix = auth && matrix.length > 0
  const overallN = hasPlan ? 3 : 2
  const systemN = overallN + 1
  const dataN = systemN + 1
  const roleN = hasRoleMatrix ? dataN + 1 : null
  const uiN = (roleN || dataN) + 1
  const riskN = uiN + 1
  const acceptanceN = riskN + 1

  return (
    <div className="mx-auto max-w-[1120px] pb-12">
      <Summary>
        <Stat n={list(doc.functional_requirements).length} label="requirements" />
        <Stat n={list(doc.roles).length} label="roles" />
        <Stat n={list(doc.main_modules).length} label="modules" />
        <Stat n={tables.length} label="tables" />
        <Stat n={list(doc.ambiguities).length} label="ambiguities"
              tone={list(doc.ambiguities).length ? 'text-warn' : undefined} />
      </Summary>

      <div className="mb-7 grid gap-6 border-b border-line2 pb-6 md:grid-cols-[minmax(0,1fr)_minmax(0,300px)]">
        <div className="min-w-0">
          <p className="font-display text-[21px] font-extrabold tracking-[-.015em] text-ink">
            {title}
          </p>
          <p className="mt-1 text-[13px] font-semibold text-label">{projectName}</p>
          <p className="mt-3 max-w-[720px] text-[12.5px] leading-[1.62] text-muted">{blurb}</p>
          {goal && <p className="mt-2 max-w-[720px] text-[12px] leading-[1.6] text-label"><b className="text-ink">Goal:</b> {goal}</p>}
          <div className="mt-4 flex flex-wrap gap-[7px]">
            {doc.system_category && <Tag>{doc.system_category}</Tag>}
            {doc.version && <Tag>v{doc.version}</Tag>}
            {doc.document_language && <Tag>{doc.document_language}</Tag>}
            {auth && <Tag tone="accent">auth</Tag>}
          </div>
        </div>
        <div className="min-w-0 md:border-l md:border-line2 md:pl-5">
          <Fact label="Version">{srs.version || doc.version || '—'}</Fact>
          <Fact label="Status">{srs.status || doc.document_control?.status || 'adopted'}</Fact>
          <Fact label="Document ID">{doc.document_control?.document_id || '—'}</Fact>
          <Fact label="Prepared">{doc.document_control?.prepared_date || '—'}</Fact>
          <Fact label="Source">{list(srs.interview?.answers).length ? `${list(srs.interview.answers).length} answers` : 'from the brief'}</Fact>
          <Fact label="Revisions">{list(srs.versions).length || 1}</Fact>
        </div>
      </div>

      <DocumentToc auth={hasRoleMatrix} hasPlan={hasPlan} />

      <DocSection id="document-control" n="0" title="Document Control">
        <DocumentControl doc={doc} srs={srs} />
      </DocSection>

      <DocSection id="introduction" n="1" title="Introduction">
        <DocSubsection title="1.1 Purpose">
          <DocParagraph>This document specifies the requirements for {projectName}. {goal}</DocParagraph>
        </DocSubsection>
        <DocSubsection title="1.2 Scope"><DocParagraph>{blurb || 'No separate scope statement was recorded.'}</DocParagraph></DocSubsection>
        <DocSubsection title="1.3 Definitions, Acronyms and Abbreviations">
          <DocParagraph>SRS — Software Requirements Specification; FR — Functional Requirement; NFR — Non-Functional Requirement; {auth ? 'RBAC — Role-Based Access Control; ' : ''}RTM — Requirement Traceability Matrix; PWA — Progressive Web App.</DocParagraph>
        </DocSubsection>
        <DocSubsection title="1.4 References">
          <DocParagraph>{doc.standards_profile?.requirements_standard || 'ISO/IEC/IEEE 29148:2018'} — requirements engineering; {doc.standards_profile?.uml_standard || 'OMG UML 2.5.1'}; {doc.standards_profile?.bpmn_standard || 'OMG BPMN 2.0.2'}; {doc.standards_profile?.erd_notation || "Crow's Foot ERD"}; {doc.standards_profile?.dfd_notation || 'Yourdon/DeMarco-style DFD'}.</DocParagraph>
        </DocSubsection>
        <DocSubsection title="1.5 Overview">
          <DocParagraph>{Object.keys(plan).length ? 'The approved plan is restated first. The remaining sections describe the overall product, detailed requirements, data design, access control, UI/UX, risks and acceptance criteria.' : 'The remaining sections describe the overall product, detailed requirements, data design, access control, UI/UX, risks and acceptance criteria.'}</DocParagraph>
        </DocSubsection>
      </DocSection>

      {hasPlan && <ApprovedPlanSection plan={plan} />}

      <DocSection id="overall-description" n={overallN} title="Overall Description">
        <DocSubsection title="Product Perspective">
          <DocParagraph>The product is a {doc.app_type?.primary_type || 'web application'}{doc.app_type?.key ? ` (${doc.app_type.key})` : ''}.</DocParagraph>
        </DocSubsection>
        <DocSubsection title="Product Functions"><Bullets items={doc.main_modules} /></DocSubsection>
        <DocSubsection title="User Characteristics">
          {auth || list(doc.roles).length > 1 ? <RoleSummary roles={doc.roles} /> : <DocParagraph>There are no accounts and no roles. Anyone who opens the application can use all of it.</DocParagraph>}
        </DocSubsection>
        <DocSubsection title="Constraints"><Bullets items={doc.constraints} /></DocSubsection>
        <DocSubsection title="Assumptions and Dependencies"><Bullets items={doc.assumptions} /></DocSubsection>
      </DocSection>

      <DocSection id="system-requirements" n={systemN} title="System Requirements">
        <DocSubsection title="Functional Requirements">
          <RequirementTable items={doc.functional_requirements} functional />
        </DocSubsection>
        <DocSubsection title="Non-Functional Requirements">
          <RequirementTable items={doc.non_functional_requirements} />
        </DocSubsection>
        <DocSubsection title="Security Requirements"><Bullets items={doc.security_requirements} /></DocSubsection>
        <DocSubsection title="External Interface & Integration Requirements">
          <IntegrationTable items={doc.integration_requirements} />
        </DocSubsection>
        <DocSubsection title="Business Workflows"><WorkflowList items={doc.business_workflows} /></DocSubsection>
        <DocSubsection title="Requirement Traceability Matrix"><TraceabilityTable items={doc.requirement_traceability_matrix} /></DocSubsection>
        <DocSubsection title="Requirements Quality Review">
          {reviewItems.length ? <QualityReview items={reviewItems} /> : <DocParagraph>No automated wording or verification lint warnings were found. Stakeholder review is still required before a formal baseline is approved.</DocParagraph>}
        </DocSubsection>
      </DocSection>

      <DocSection id="data-requirements" n={dataN} title="Data Requirements and Database Design">
        {tables.length ? tables.map((table, i) => <DatabaseTable key={table.table_name || i} table={table} index={i} />) : <DocParagraph>No persistent business data is required by this specification.</DocParagraph>}
        {relationships.length > 0 && <DocSubsection title="Relationships"><RelationshipTable items={relationships} /></DocSubsection>}
      </DocSection>

      {auth && matrix.length > 0 && (
        <DocSection id="role-access" n={roleN} title="Role Access Matrix">
          <RoleAccessTable items={matrix} />
        </DocSection>
      )}

      <DocSection id="ui-ux" n={uiN} title="UI/UX Requirements">
        <UiUxSection doc={doc} />
      </DocSection>

      <DocSection id="risks" n={riskN} title="Risks and Priorities">
        <RiskList items={doc.risk_priority} />
      </DocSection>

      <DocSection id="acceptance" n={acceptanceN} title="Acceptance Criteria">
        <AcceptanceList items={doc.acceptance_criteria} />
      </DocSection>
    </div>
  )
}

function DocumentToc({ auth, hasPlan }) {
  const entries = [
    ['document-control', 'Document Control'],
    ['introduction', 'Introduction'],
    ...(hasPlan ? [['approved-plan', 'Approved Plan']] : []),
    ['overall-description', 'Overall Description'],
    ['system-requirements', 'System Requirements'],
    ['data-requirements', 'Data Requirements and Database Design'],
    ...(auth ? [['role-access', 'Role Access Matrix']] : []),
    ['ui-ux', 'UI/UX Requirements'],
    ['risks', 'Risks and Priorities'],
    ['acceptance', 'Acceptance Criteria'],
  ]
  return (
    <div className="mb-8 rounded-[18px] bg-black/[.022] p-4 ring-1 ring-line/70 dark:bg-white/[.025]">
      <p className="label-xs mb-2.5 text-ink">Document contents</p>
      <div className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2 lg:grid-cols-3">
        {entries.map(([id, label], i) => <a key={id} href={`#${id}`} className="text-[11.5px] text-muted hover:text-accent"><span className="mr-2 font-mono text-[10px] text-faint">{String(i + 1).padStart(2, '0')}</span>{label}</a>)}
      </div>
      <p className="mt-3 text-[10.5px] text-muted2">The diagram appendix remains in the Diagrams tab and PDF; the document view shows the complete textual specification in reading order.</p>
    </div>
  )
}

function DocSection({ id, n, title, children }) {
  return (
    <section id={id} className="scroll-mt-4 border-t-2 border-line2 py-6 first:border-t-0">
      <div className="mb-4 flex items-baseline gap-3">
        <span className="font-mono text-[11px] text-accent">{n}</span>
        <h2 className="font-display text-[17px] font-extrabold tracking-[-.01em] text-ink">{title}</h2>
      </div>
      {children}
    </section>
  )
}

function DocSubsection({ title, children }) {
  return (
    <div className="mb-5 last:mb-0">
      <h3 className="mb-2 text-[12px] font-bold text-ink">{title}</h3>
      {children}
    </div>
  )
}

const DocParagraph = ({ children }) => <p className="max-w-[900px] text-[12px] leading-[1.65] text-muted">{children}</p>

function DocumentControl({ doc, srs }) {
  const dc = doc.document_control || {}
  const rows = [
    ['Document ID', dc.document_id || 'SRS'],
    ['Version', dc.version || doc.version || srs.version || '1.0.0'],
    ['Status', srs.status || dc.status || 'Draft'],
    ['Prepared Date', dc.prepared_date || '—'],
    ['Document Owner', dc.document_owner || 'Project Stakeholders'],
    ['Standard Profile', doc.standards_profile?.requirements_standard || dc.standard || 'ISO/IEC/IEEE 29148:2018'],
  ]
  return (
    <>
      <div className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">{rows.map(([k, v]) => <div key={k} className="rounded-xl bg-black/[.022] px-3 py-2 dark:bg-white/[.025]"><p className="text-[10px] uppercase tracking-[.08em] text-muted2">{k}</p><p className="mt-1 text-[11.5px] font-medium text-ink">{String(v)}</p></div>)}</div>
      {doc.standards_profile?.conformance_note && <p className="mt-3 text-[10.5px] leading-relaxed text-muted2">{doc.standards_profile.conformance_note}</p>}
      <DocSubsection title="Revision History"><RevisionTable items={doc.revision_history} /></DocSubsection>
      <DocSubsection title="Approval Record">{list(doc.approval_record).length ? <ApprovalTable items={doc.approval_record} /> : <DocParagraph>Formal stakeholder approval has not yet been recorded in this baseline.</DocParagraph>}</DocSubsection>
    </>
  )
}

function ApprovedPlanSection({ plan }) {
  return (
    <DocSection id="approved-plan" n="2" title="Approved Plan">
      {plan.product_intent && <DocSubsection title="2.1 Product Intent"><DocParagraph>{plan.product_intent}</DocParagraph></DocSubsection>}
      <DocSubsection title="2.2 Users"><PlanUsers items={plan.users} /></DocSubsection>
      <DocSubsection title="2.3 Screens"><PlanScreens items={plan.screens} /></DocSubsection>
      <DocSubsection title="2.4 Records"><PlanRecords items={plan.records} /></DocSubsection>
      <DocSubsection title="2.5 Features"><Bullets items={plan.features} /></DocSubsection>
      <DocSubsection title="2.6 Workflows"><PlanWorkflows items={plan.workflows} /></DocSubsection>
      {plan.look_and_feel && <DocSubsection title="2.7 Look and Feel"><DocParagraph>{plan.look_and_feel}</DocParagraph></DocSubsection>}
      <DocSubsection title="2.8 What We Assumed"><Bullets items={plan.assumptions} /></DocSubsection>
      {list(plan.open_questions).length > 0 && <DocSubsection title="2.9 Still to Settle"><Bullets items={plan.open_questions.map(q => ({ id: q.required ? 'required' : 'open', text: q.question }))} tone="text-warn" /></DocSubsection>}
    </DocSection>
  )
}

function RequirementTable({ items, functional }) {
  const rows = list(items)
  if (!rows.length) return <DocParagraph>None recorded.</DocParagraph>
  return <Table><thead><TR><TH>ID</TH><TH>{functional ? 'Module' : 'Category'}</TH><TH>Requirement</TH>{functional && <TH>Priority</TH>}<TH>Verification</TH></TR></thead><tbody>{rows.map((r, i) => <TR key={r.id || i}><TD className="font-mono text-[10.5px]">{r.id || `#${i + 1}`}</TD><TD>{functional ? r.module : r.category}</TD><TD className="min-w-[360px]">{r.requirement}</TD>{functional && <TD className="capitalize text-muted">{r.priority || 'medium'}</TD>}<TD className="text-muted">{r.verification_method || (functional ? 'Functional Test' : 'Test / Analysis')}</TD></TR>)}</tbody></Table>
}

function IntegrationTable({ items }) {
  const rows = list(items)
  if (!rows.length) return <DocParagraph>No external integrations are required for the initial release.</DocParagraph>
  return <Table><thead><TR><TH>Integration</TH><TH>Type</TH><TH>Description</TH></TR></thead><tbody>{rows.map((r, i) => <TR key={r.name || i}><TD>{r.name}</TD><TD className="text-muted">{r.type}</TD><TD>{r.description}</TD></TR>)}</tbody></Table>
}

function WorkflowList({ items }) {
  const rows = list(items)
  if (!rows.length) return <DocParagraph>No separate business workflow was recorded.</DocParagraph>
  return <div className="space-y-3">{rows.map((wf, i) => <div key={wf.workflow_name || i} className="rounded-xl bg-black/[.02] p-3 ring-1 ring-line/60 dark:bg-white/[.022]"><p className="text-[11.5px] font-semibold text-ink">{wf.workflow_name || `Workflow ${i + 1}`}{wf.who ? <span className="ml-2 font-normal text-muted">— {wf.who}</span> : null}</p><ol className="mt-2 space-y-1">{list(wf.steps).map((step, j) => <li key={j} className="grid grid-cols-[24px_1fr] gap-2 text-[11.5px] leading-relaxed text-muted"><span className="font-mono text-faint">{j + 1}.</span><span>{step}</span></li>)}</ol></div>)}</div>
}

function TraceabilityTable({ items }) {
  const rows = list(items)
  if (!rows.length) return <DocParagraph>No traceability rows were recorded.</DocParagraph>
  return <Table><thead><TR><TH>Req</TH><TH>Source / Module</TH><TH>Design / Data</TH><TH>Verification</TH><TH>Test</TH></TR></thead><tbody>{rows.slice(0, 80).map((r, i) => <TR key={`${r.requirement_id}-${i}`}><TD className="font-mono">{r.requirement_id}</TD><TD>{r.source || r.module || 'Approved SRS'}</TD><TD className="text-muted">{[...list(r.pages), ...list(r.tables)].slice(0, 4).join(', ') || '—'}</TD><TD className="text-muted">{r.verification_method || 'Functional Test'}</TD><TD className="font-mono text-muted">{r.test_case || '—'}</TD></TR>)}</tbody></Table>
}

function QualityReview({ items }) {
  return <Table><thead><TR><TH>Requirement</TH><TH>Review warning</TH></TR></thead><tbody>{list(items).map((r, i) => <TR key={r.requirement_id || i}><TD className="font-mono">{r.requirement_id || `#${i + 1}`}</TD><TD>{list(r.warnings).join('; ')}</TD></TR>)}</tbody></Table>
}

function DatabaseTable({ table, index }) {
  const fields = list(table.fields || table.columns)
  return <DocSubsection title={`${index + 1}. ${table.table_name || table.name || `Table ${index + 1}`}`}>{table.description && <DocParagraph>{table.description}</DocParagraph>}<div className={table.description ? 'mt-2' : ''}><Table><thead><TR><TH>Field</TH><TH>Type</TH><TH>Key / Notes</TH></TR></thead><tbody>{fields.map((f, i) => { const notes = [f.primary_key && 'PK', f.type === 'foreign_key' && f.references && `FK→${f.references}`, f.unique && 'unique', f.nullable === false && 'required', list(f.values).length && `enum: ${list(f.values).slice(0, 5).join(', ')}`, f.default != null && `default=${String(f.default)}`].filter(Boolean).join(', '); return <TR key={f.name || i}><TD>{typeof f === 'string' ? f : f.name}</TD><TD className="text-muted">{typeof f === 'object' ? f.type : ''}</TD><TD className="text-muted">{notes}</TD></TR> })}</tbody></Table></div></DocSubsection>
}

function RelationshipTable({ items }) {
  return <Table><thead><TR><TH>From</TH><TH>To</TH><TH>Type</TH><TH>Description</TH></TR></thead><tbody>{list(items).map((r, i) => <TR key={i}><TD>{r.from || r.from_}</TD><TD>{r.to}</TD><TD className="text-muted">{r.type}</TD><TD>{r.description || '—'}</TD></TR>)}</tbody></Table>
}

function RoleSummary({ roles }) {
  return <Table><thead><TR><TH>Role</TH><TH>Description</TH></TR></thead><tbody>{list(roles).map((r, i) => <TR key={r.role_key || i}><TD>{r.role_name || r.name || line(r)}</TD><TD className="text-muted">{r.description || '—'}</TD></TR>)}</tbody></Table>
}

function RoleAccessTable({ items }) {
  return <Table><thead><TR><TH>Role</TH><TH>Allowed Pages</TH><TH>Allowed Functions</TH></TR></thead><tbody>{list(items).map((r, i) => <TR key={r.role || i}><TD>{r.role || r.role_name}</TD><TD className="text-muted">{list(r.allowed_pages || r.pages).map(line).join(', ') || '—'}</TD><TD className="text-muted">{list(r.allowed_functions || r.permissions || r.access).map(line).join(', ') || '—'}</TD></TR>)}</tbody></Table>
}

function UiUxSection({ doc }) {
  const ui = doc.ui_ux_requirements || {}
  const brand = doc.branding || {}
  return <div className="space-y-3"><KeyValue label="Design Style" value={ui.design_style} /><KeyValue label="Theme" value={ui.theme || brand.theme} /><KeyValue label="Dashboard Layout" value={ui.dashboard_layout} />{list(ui.required_components).length > 0 && <KeyValue label="Components" value={list(ui.required_components).join(', ')} />}{brand.palette && <KeyValue label="Palette" value={`${brand.palette}${brand.primary_color ? ` (${brand.primary_color})` : ''}`} />}{brand.logo_required && <KeyValue label="Logo" value={`Generated artwork requested${brand.logo_image_prompt ? ` — ${brand.logo_image_prompt}` : ''}`} />}</div>
}

function KeyValue({ label, value }) {
  if (value == null || value === '' || (Array.isArray(value) && !value.length)) return null
  return <div className="grid gap-2 border-b border-line py-2 sm:grid-cols-[170px_1fr]"><span className="text-[11px] font-semibold text-label">{label}</span><span className="text-[12px] leading-relaxed text-ink">{Array.isArray(value) ? value.join(', ') : String(value)}</span></div>
}

function RiskList({ items }) {
  const rows = list(items)
  if (!rows.length) return <DocParagraph>No specific project risks were recorded.</DocParagraph>
  return <div className="space-y-3">{rows.map((r, i) => <div key={r.id || i} className="rounded-xl border border-line p-3"><div className="flex items-center gap-2"><Tag tone={String(r.severity || '').toLowerCase() === 'high' ? 'solid' : undefined}>{r.severity || 'Medium'} risk</Tag><p className="text-[12px] font-semibold text-ink">{r.risk || line(r)}</p></div>{r.reason && <p className="mt-2 text-[11.5px] leading-relaxed text-muted">{r.reason}</p>}{r.mitigation && <p className="mt-1 text-[11.5px] leading-relaxed text-label"><b>Mitigation:</b> {r.mitigation}</p>}</div>)}</div>
}

function AcceptanceList({ items }) {
  const rows = list(items)
  if (!rows.length) return <DocParagraph>No acceptance criteria were recorded.</DocParagraph>
  return <ul>{rows.map((r, i) => <li key={r.id || i} className="grid grid-cols-[28px_1fr] gap-2 border-b border-line py-2 last:border-0"><span className="text-ok">✓</span><span className="text-[12px] leading-relaxed text-ink">{typeof r === 'string' ? r : (r.criterion || line(r))}</span></li>)}</ul>
}

function RevisionTable({ items }) {
  const rows = list(items)
  if (!rows.length) return <DocParagraph>No revision history was recorded.</DocParagraph>
  return <Table><thead><TR><TH>Version</TH><TH>Date</TH><TH>Description</TH></TR></thead><tbody>{rows.map((r, i) => <TR key={i}><TD>{r.version}</TD><TD className="text-muted">{r.date}</TD><TD>{r.description}</TD></TR>)}</tbody></Table>
}

function ApprovalTable({ items }) {
  return <Table><thead><TR><TH>Role</TH><TH>Name</TH><TH>Date</TH><TH>Status</TH></TR></thead><tbody>{list(items).map((r, i) => <TR key={i}><TD>{r.role}</TD><TD>{r.name}</TD><TD className="text-muted">{r.date}</TD><TD>{r.status}</TD></TR>)}</tbody></Table>
}

function PlanUsers({ items }) {
  const rows = list(items)
  if (!rows.length) return <DocParagraph>No distinct user groups were recorded.</DocParagraph>
  return <Table><thead><TR><TH>User</TH><TH>Can do</TH></TR></thead><tbody>{rows.map((r, i) => <TR key={i}><TD>{r.role || r.name}</TD><TD className="text-muted">{list(r.can_do).join(', ') || '—'}</TD></TR>)}</tbody></Table>
}

function PlanScreens({ items }) {
  const rows = list(items)
  if (!rows.length) return <DocParagraph>No screens were recorded.</DocParagraph>
  return <Table><thead><TR><TH>Screen</TH><TH>Route</TH><TH>Purpose</TH><TH>Who</TH></TR></thead><tbody>{rows.map((r, i) => <TR key={i}><TD>{r.name}</TD><TD className="font-mono text-muted">{r.route}</TD><TD>{r.purpose}</TD><TD className="text-muted">{list(r.who).join(', ') || '—'}</TD></TR>)}</tbody></Table>
}

function PlanRecords({ items }) {
  const rows = list(items)
  if (!rows.length) return <DocParagraph>No persistent records were included in the approved plan.</DocParagraph>
  return <Table><thead><TR><TH>Record</TH><TH>Information kept</TH></TR></thead><tbody>{rows.map((r, i) => <TR key={i}><TD>{r.name}</TD><TD className="text-muted">{list(r.keeps).join(', ') || '—'}</TD></TR>)}</tbody></Table>
}

function PlanWorkflows({ items }) {
  const rows = list(items)
  if (!rows.length) return <DocParagraph>No explicit workflows were recorded in the plan.</DocParagraph>
  return <div className="space-y-2">{rows.map((r, i) => <div key={i} className="rounded-xl bg-black/[.02] p-3 dark:bg-white/[.02]"><p className="text-[11.5px] font-semibold text-ink">{r.name || `Workflow ${i + 1}`}</p>{r.who && <p className="mt-0.5 text-[10.5px] text-muted">Actor: {r.who}</p>}<ol className="mt-2 space-y-1">{list(r.steps).map((s, j) => <li key={j} className="text-[11.5px] text-muted">{j + 1}. {s}</li>)}</ol></div>)}</div>
}


/** One row of the document's own facts: label at the left, value at the right. */
const Fact = ({ label, children }) => (
  <div className="flex items-baseline justify-between gap-3 border-b border-line
                  py-1.5 text-[11.5px]">
    <span className="text-label">{label}</span>
    <span className="min-w-0 truncate font-mono text-[11px] text-ink">{children}</span>
  </div>
)

export function Plan({ srs }) {
  if (!srs.have?.plan) return <Empty>No approved plan was saved with this SRS.</Empty>
  return (
    <pre className="whitespace-pre-wrap break-words border border-line2 bg-panel2
                    p-4 font-mono text-[12px] leading-[1.7] text-ink">
      {srs.plan}
    </pre>
  )
}

export function Requirements({ srs }) {
  const doc = srs.document || {}
  const functional = list(doc.functional_requirements)
  const nonFunctional = list(doc.non_functional_requirements)
  if (!srs.have?.document) return <Empty>No SRS document was adopted for this project.</Empty>
  return (
    <Columns>
      <Section n="1" title="Functional" count={functional.length}>
        <Bullets items={functional} tone="text-accent" />
      </Section>
      <Section n="2" title="Non-functional" count={nonFunctional.length}>
        <Bullets items={nonFunctional} />
      </Section>
      <Section n="3" title="Validation rules" count={list(doc.validation_rules).length}>
        <Bullets items={doc.validation_rules} />
      </Section>
      <Section n="4" title="Security" count={list(doc.security_requirements).length}>
        <Bullets items={doc.security_requirements} />
      </Section>
    </Columns>
  )
}

export function Data({ srs }) {
  const tables = list((srs.document || {}).database_design?.tables)
  if (!tables.length) return <Empty>No database design in this SRS.</Empty>
  return (
    <>
      {tables.map((table, i) => {

        const columns = list(table.fields || table.columns)
        return (
          <Section key={i}
                   title={table.table_name || table.name || `Table ${i + 1}`}
                   count={columns.length}>
            {table.description && (
              <p className="mb-2 text-[11.5px] text-muted">{table.description}</p>
            )}
            <Table>
              <thead>
                <TR><TH>Column</TH><TH>Type</TH><TH>Notes</TH></TR>
              </thead>
              <tbody>
                {columns.map((c, j) => (
                  <TR key={j}>
                    <TD>{typeof c === 'string' ? c : (c.name || c.column)}</TD>
                    <TD className="text-muted">{typeof c === 'object' ? (c.type || '') : ''}</TD>
                    <TD className="text-muted">
                      {typeof c === 'object' ? [
                        c.primary_key && 'primary key',
                        c.unique && 'unique',
                        c.nullable === false && 'required',
                        c.references && `→ ${c.references}`,
                        c.description,
                      ].filter(Boolean).join(', ') : ''}
                    </TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          </Section>
        )
      })}
      {list((srs.document || {}).database_design?.relationships).length > 0 && (
        <Section title="Relationships"
                 count={list(srs.document.database_design.relationships).length}>
          <Bullets items={srs.document.database_design.relationships} />
        </Section>
      )}
    </>
  )
}

export function Roles({ srs }) {
  const doc = srs.document || {}
  const matrix = list(doc.role_access_matrix)
  if (!list(doc.roles).length && !matrix.length) {
    return <Empty>This SRS describes no roles — every page is public.</Empty>
  }
  return (
    <>
      <Section title="Roles" count={list(doc.roles).length}>
        {list(doc.roles).length ? (
          <ul>
            {list(doc.roles).map((r, i) => (
              <li key={i} className="border-b border-line py-[7px] text-[12px]
                                     leading-[1.5] last:border-0">
                <b className="font-extrabold text-ink">
                  {r.role_name || r.name || r.role || idOf(r, i)}
                </b>
                {r.description && <span className="text-muted"> — {r.description}</span>}
              </li>
            ))}
          </ul>
        ) : <p className="py-2 text-[11.5px] text-muted2">None recorded.</p>}
      </Section>
      {matrix.length > 0 && (
        <Section title="Who can reach what" count={matrix.length}>
          <Table>
            <thead><TR><TH>Role</TH><TH>Pages</TH><TH>Can do</TH></TR></thead>
            <tbody>
              {matrix.map((row, i) => (
                <TR key={i}>
                  <TD>{row.role || row.role_name || idOf(row, i)}</TD>
                  <TD className="text-muted">
                    {list(row.allowed_pages || row.pages).map(line).join(', ') || '—'}
                  </TD>
                  <TD className="text-muted">
                    {list(row.allowed_functions || row.permissions || row.access)
                      .map(line).join(', ') || '—'}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Section>
      )}
      <Section title="Public pages" count={list(doc.public_pages).length}>
        <Bullets items={doc.public_pages} />
      </Section>
      <Section title="Protected pages" count={list(doc.protected_pages).length}>
        <Bullets items={doc.protected_pages} tone="text-warn" />
      </Section>
    </>
  )
}

export function Handoff({ srs }) {
  const handoff = srs.handoff || {}
  const [copied, setCopied] = useState(false)
  if (!srs.have?.handoff) return <Empty>No builder handoff was saved with this SRS.</Empty>

  async function copy() {
    try {
      await navigator.clipboard.writeText(handoff.prompt || '')
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch {  }
  }

  return (
    <>
      <Summary>
        <Stat n={list(handoff.requirements).length} label="requirements" />
        <Stat n={list(handoff.pages).length} label="pages" />
        <Stat n={list(handoff.models).length} label="models" />
      </Summary>
      <Section title={`The prompt this app was built from — ${handoff.appType || ''}`}>
        <div className="relative mt-2 border border-line2 bg-panel2 p-4">
          <button onClick={copy}
                  className="absolute right-3 top-3 border border-line2 bg-panel
                             px-2 py-1 font-mono text-[10px] text-muted
                             transition-colors hover:bg-ink/[.07] hover:text-ink">
            {copied ? 'Copied' : 'Copy'}
          </button>
          <pre className="whitespace-pre-wrap break-words pr-16 font-mono
                          text-[12px] leading-[1.7] text-ink">
            {handoff.prompt}
          </pre>
        </div>
      </Section>
    </>
  )
}

export function Diagrams({ srs }) {
  const diagrams = list(srs.diagrams)
  const [open, setOpen] = useState(0)
  const [zoomed, setZoomed] = useState(false)
  if (!diagrams.length) return <Empty>No diagrams were saved with this SRS.</Empty>
  const current = diagrams[Math.min(open, diagrams.length - 1)]
  return (
    <>
      <div className="mb-4 flex w-fit flex-wrap border border-line2">
        {diagrams.map((d, i) => (
          <button key={d.name} onClick={() => setOpen(i)}
                  className={cn('border-r border-line2 px-3 py-1.5 text-[11px]',
                    'font-semibold capitalize transition-colors last:border-r-0',
                    i === open ? 'bg-accent text-bg'
                               : 'text-label hover:bg-ink/[.07] hover:text-ink')}>
            {d.name.replace(/_/g, ' ')}
          </button>
        ))}
      </div>
      <div className="group relative overflow-x-auto rounded-[14px] border border-line2 bg-panel2 p-4">
        {current.svg
          ? <>
              {/* The whole picture is the target, not a corner icon: at this
                  size the diagram is a thumbnail, and clicking a thumbnail to
                  enlarge it is the thing people already try. */}
              <button onClick={() => setZoomed(true)}
                      title="Open this diagram full size"
                      className="block w-full cursor-zoom-in text-left">
                <div className="srs-diagram [&_svg]:h-auto [&_svg]:max-w-full"
                     dangerouslySetInnerHTML={{ __html: current.svg }} />
              </button>
              <span className="pointer-events-none absolute right-3 top-3 flex items-center gap-1.5 rounded-full bg-ink/75 px-2.5 py-1 text-[10.5px] font-medium text-bg opacity-0 transition-opacity group-hover:opacity-100">
                <Maximize2 className="size-3" /> Click to enlarge
              </span>
            </>
          : <pre className="whitespace-pre font-mono text-[11.5px] text-muted">
              {current.mermaid}
            </pre>}
      </div>

      {zoomed && current.svg && (
        <DiagramViewer svg={current.svg} title={current.name}
                       onClose={() => setZoomed(false)} />
      )}
      {!current.svg && (
        <p className="mt-2 text-[11px] text-muted2">
          {current.rendered
            ? 'The picture for this revision is on disk but only the current '
              + 'version is displayed — its Mermaid source is shown instead.'
            : 'No image was rendered for this one — the Mermaid source is shown instead.'}
        </p>
      )}
    </>
  )
}

export function Interview({ srs }) {
  const transcript = list((srs.interview || {}).transcript)
  const answers = list((srs.interview || {}).answers)
  if (!transcript.length) return <Empty>No interview was saved with this SRS.</Empty>
  const byId = Object.fromEntries(answers.map(a => [a.question_id, a]))
  return (
    <div className="border border-line2">
      {transcript.map((q, i) => {
        const a = byId[q.id]
        const value = a && (Array.isArray(a.value) ? a.value.join(', ') : a.value)
        return (
          <div key={q.id || i}
               className="grid grid-cols-[34px_1fr] gap-2.5 border-b border-line
                          px-3 py-2.5 last:border-0">
            <span className="font-mono text-[10.5px] text-faint">
              {String(i + 1).padStart(2, '0')}
            </span>
            <div className="min-w-0">
              <p className="text-[12px] leading-[1.5] text-ink">{q.question}</p>
              <p className="mt-1.5 border-l-[3px] border-accent pl-2.5 text-[12px]
                            text-ink">
                {value != null && String(value).trim()
                  ? String(value)
                  : <span className="text-muted2">not answered</span>}
              </p>
              {a?.raw_text && a.raw_text !== value && (
                <p className="mt-1 pl-[13px] text-[11px] text-muted">“{a.raw_text}”</p>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}


export const VIEWS = [
  { id: 'document', label: 'Document', C: Document },
  { id: 'plan', label: 'Approved Plan', C: Plan },
  { id: 'requirements', label: 'Requirements', C: Requirements },
  { id: 'data', label: 'Data', C: Data },
  { id: 'roles', label: 'Roles & Access', C: Roles },
  { id: 'handoff', label: 'Handoff', C: Handoff },
  { id: 'diagrams', label: 'Diagrams', C: Diagrams },
  { id: 'interview', label: 'Interview', C: Interview },
  { id: 'risks', label: 'Risks', C: Risks },
]


export function badgeFor(id, srs) {
  const doc = srs?.document || {}
  const n = (value) => (Array.isArray(value) ? value.length : 0)
  if (id === 'requirements' && n(doc.functional_requirements)) {
    return { n: n(doc.functional_requirements), bad: false }
  }
  if (id === 'data' && n(doc.database_design?.tables)) {
    return { n: n(doc.database_design.tables), bad: false }
  }
  if (id === 'diagrams' && n(srs?.diagrams)) {
    return { n: n(srs.diagrams), bad: false }
  }
  if (id === 'interview' && n(srs?.interview?.transcript)) {
    return { n: n(srs.interview.transcript), bad: false }
  }

  if (id === 'risks' && n(doc.ambiguities)) {
    return { n: n(doc.ambiguities), bad: true }
  }
  return null
}

export function Risks({ srs }) {
  const doc = srs.document || {}
  const ambiguities = list(doc.ambiguities)
  const risks = list(doc.risk_priority)
  if (!srs.have?.document) return <Empty>No SRS document was adopted for this project.</Empty>
  return (
    <>
      <Section n="1" title="Ambiguities" count={ambiguities.length}>
        {ambiguities.length ? (
          <ul>
            {ambiguities.map((a, i) => (
              <li key={i} className="grid grid-cols-[34px_1fr] gap-2.5 border-b
                                     border-line py-[7px] last:border-0">
                <span className="font-mono text-[10.5px] text-accent">
                  {a.area || `Q${i + 1}`}
                </span>
                <span className="text-[12px] leading-[1.5]">
                  <span className="text-ink">{a.description || line(a)}</span>
                  {a.assumption_made && (
                    <span className="mt-0.5 block text-muted">
                      Assumed: {a.assumption_made}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="py-2 text-[12px] text-ok">
            Nothing was left ambiguous — every question the SRS raised was settled.
          </p>
        )}
        {ambiguities.length > 0 && (
          <p className="mt-3 border-l-[3px] border-accent bg-tint px-3 py-2
                        text-[11.5px] text-deep">
            {ambiguities.length === 1
              ? 'One ambiguity is still open.'
              : `${ambiguities.length} ambiguities are still open.`}{' '}
            The build used the safest reading of each.
          </p>
        )}
      </Section>
      <Section n="2" title="Risks" count={risks.length}>
        <Bullets items={risks} tone="text-accent" />
      </Section>
      <Section n="3" title="Acceptance criteria"
               count={list(doc.acceptance_criteria).length}>
        <Bullets items={doc.acceptance_criteria} />
      </Section>
    </>
  )
}
