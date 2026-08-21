
const SHARED_HEAD = [
  { id: 'intake', title: 'Project intake',
    detail: 'Find the app root, the environment, the routes and the repo.' },
  { id: 'planner', title: 'Deployment plan',
    detail: 'A model writes a redacted, structured plan of what to deploy.' },
  { id: 'runtime', title: 'Runtime assets',
    detail: 'The environment contract and the release script.' },
  { id: 'cicd', title: 'GitHub Actions',
    detail: 'CI validation and the deployment workflow.' },
]

const SHARED_TAIL = [
  { id: 'security', title: 'Security validation',
    detail: 'Secret boundaries, OIDC scope, and every artifact present.' },
  { id: 'repair', title: 'Compatibility repair',
    detail: 'Build feedback, and only the repairs it is allowed to make.' },
]

export const PIPELINE = {
  vercel: [
    ...SHARED_HEAD,
    { id: 'provider', title: 'Vercel artifacts',
      detail: 'vercel.json and the production environment mapping.' },
    ...SHARED_TAIL,
    { id: 'link', title: 'Vercel project',
      detail: 'Create or link the project and read its identifiers.' },
    { id: 'env', title: 'Vercel environment',
      detail: 'Production variables, set on Vercel rather than in GitHub.' },
    { id: 'github', title: 'GitHub delivery',
      detail: 'Commit the reviewed files and run CI.' },
    { id: 'deploy', title: 'Vercel release',
      detail: 'Build and deploy the prebuilt output.' },
    { id: 'validation', title: 'API validation',
      detail: 'Probe the homepage and the generated health endpoint.' },
  ],
  aws_ecs: [
    ...SHARED_HEAD,

    { id: 'aws', title: 'Container artifacts',
      detail: 'The Dockerfile, the task definition, and the ECR/ECS stack.' },
    ...SHARED_TAIL,
    { id: 'bootstrap', title: 'CloudFormation bootstrap',
      detail: 'ECR, the Fargate cluster and service, and the load balancer.' },
    { id: 'secrets', title: 'Runtime secrets',
      detail: 'Written to Secrets Manager; the task execution role reads them.' },
    { id: 'github', title: 'GitHub delivery',
      detail: 'Commit the reviewed files and run CI.' },
    { id: 'deploy', title: 'Image build and rollout',
      detail: 'Built on the runner, pushed to ECR, rolled onto the service.' },
    { id: 'validation', title: 'API validation',
      detail: 'Probe the homepage and the generated health endpoint.' },
  ],
  aws_ec2: [
    ...SHARED_HEAD,
    { id: 'aws', title: 'AWS infrastructure',
      detail: 'The CloudFormation bootstrap stack: EC2, S3 and IAM.' },
    ...SHARED_TAIL,
    { id: 'bootstrap', title: 'CloudFormation bootstrap',
      detail: 'Networking, EC2, an Elastic IP, S3, IAM, logs and secrets.' },
    { id: 'secrets', title: 'Runtime secrets',
      detail: 'Written straight to AWS Secrets Manager, never to GitHub.' },
    { id: 'github', title: 'GitHub delivery',
      detail: 'Commit the reviewed files and run CI.' },
    { id: 'deploy', title: 'EC2 release',
      detail: 'Upload the build to S3 and run the release script over SSM.' },
    { id: 'validation', title: 'API validation',
      detail: 'Probe the homepage and the generated health endpoint.' },
  ],
}

export const TARGETS = [
  { id: 'vercel', label: 'Vercel',
    blurb: 'A Vercel project, deployed from GitHub. Nothing to run, nothing '
         + 'to pay for while it is idle.' },
  { id: 'aws_ecs', label: 'AWS ECS',
    blurb: 'A Docker image built by GitHub Actions — never on this machine — '
         + 'pushed to ECR and run on Fargate behind a load balancer, so the '
         + 'URL never changes. Costs more than EC2 and is NOT free tier.' },
  { id: 'aws_ec2', label: 'AWS EC2',
    blurb: 'One small instance with an Elastic IP, nginx, and the app under '
         + 'systemd. Port 22 stays shut; administration is over SSM. '
         + 'This costs money while it is up.' },
]


export const STATE_TEXT = {
  STARTING:     ['Starting…', 'run'],
  DRAFT:        ['Queued', 'run'],
  ANALYZING:    ['Reading the project', 'run'],
  REVIEW_READY: ['Reviewed — deploying', 'run'],
  BOOTSTRAPPING:['Creating infrastructure', 'run'],
  CI_RUNNING:   ['GitHub Actions is building', 'run'],
  DEPLOYING:    ['Deploying', 'run'],
  VALIDATING:   ['Checking it answers', 'run'],
  LIVE:         ['Live', 'pass'],
  FAILED:       ['Failed', 'fail'],
  ROLLED_BACK:  ['Rolled back', 'fail'],
  DESTROYED:    ['Torn down', 'mute'],
  CANCELLED:    ['Cancelled', 'mute'],
}


export const TERMINAL = new Set(
  ['LIVE', 'FAILED', 'ROLLED_BACK', 'DESTROYED', 'CANCELLED'])


export function progressOf(events, stages) {

  const seen = Array.isArray(events) ? events : []
  const byStage = new Map()
  for (const e of seen) {
    if (e.stage) byStage.set(e.stage, e)
  }
  const rows = stages.map(s => {
    const e = byStage.get(s.id)
    const status = !e ? 'pending'
      : e.status === 'complete' ? 'done'
      : e.type === 'error' || e.status === 'failed' ? 'error'
      : 'active'
    return { ...s, status, message: e?.message || '' }
  })
  const done = rows.filter(r => r.status === 'done').length
  const newest = seen[seen.length - 1]
  const pct = Math.max(Number(newest?.percent || 0),
                       Math.round((done / Math.max(rows.length, 1)) * 100))
  return { rows, pct: Math.min(100, Math.max(0, pct)) }
}
