# KubeFit

[English](README.md) | **한국어**

KubeFit은 실제 Kubernetes 워크로드와 Prometheus 메트릭을 분석해 CPU·메모리
`requests/limits`를 추천하고, 성능 검증을 통과한 YAML 변경안을 GitHub Draft
PR로 제안하는 GitOps 기반 오픈소스 플랫폼입니다.

> 먼저 측정하고, 근거와 위험을 설명한 뒤, GitOps 검토를 거쳐 변경합니다.

![KubeFit의 counterbalanced Pair 검증 화면](docs/assets/pair-review-live.png)

오른쪽 Pair 결과는 서로 반대인 두 실행 순서의 실제 benchmark artifact를 서버가
전부 재생한 뒤 `PASS`로 판정한 결과입니다. 왼쪽 수정 가능 영역은 예제 입력이며
해당 PASS 판정의 근거가 아닙니다.

## 왜 KubeFit인가

Kubernetes 리소스를 너무 크게 잡으면 비용이 낭비되고, 너무 작게 잡으면
OOMKilled, CPU throttling, 응답 지연이 발생할 수 있습니다. 추천값만 자동 적용하면
변경 이유와 실제 성능 영향을 검토하기도 어렵습니다.

KubeFit은 운영 클러스터를 자동으로 줄이지 않습니다.

```text
Kubernetes Deployment + Prometheus 시계열
                    ↓
      P95/P99 기반 리소스 추천
                    ↓
         비용·안정성 위험 평가
                    ↓
      before/after + 반대 순서 검증
                    ↓
         YAML 최소 변경안 생성
                    ↓
          GitHub Draft PR 제안
                    ↓
             사람의 최종 승인
```

핵심 차별점은 다음 세 가지입니다.

- **설명 가능성:** 사용한 메트릭, percentile, 안전 여유와 차단 사유를 표시합니다.
- **GitOps 안전성:** 추천 경로에서는 클러스터를 변경하지 않고 검증된 제안만 Draft
  PR로 전달합니다.
- **비용과 안정성의 동시 평가:** 비용 절감 수치가 latency, OOM, throttling 위험을
  덮어쓰지 못합니다.

## MVP 범위

- Kubernetes `Deployment` 분석
- Prometheus CPU·메모리 시계열 수집
- CPU P95·메모리 P99와 안전 여유 기반 requests/limits 추천
- 현재·추천 request 비용과 운영 위험 비교
- 기존 문맥을 보존하는 Kubernetes YAML 최소 패치
- GitHub Draft PR 생성
- k6 before/after 및 반대 순서 Pair 검증
- FastAPI API와 React Decision Console
- Helm chart 및 amd64/arm64 공개 컨테이너 이미지

HPA 추천, 멀티클라우드 가격 자동 수집, 장애 예측, Terraform 생성, AI 챗봇은 현재
MVP 범위에 포함하지 않습니다.

## 변경 안전성 검증 고도화

`kubefit check`는 반대 순서로 실행한 두 개의 immutable benchmark 결과를 같은
정책으로 다시 판정합니다. 판정 JSON을 출력하고, GitHub Actions에서는
`GITHUB_STEP_SUMMARY`에 표를 추가하며, PASS가 아니면 종료 코드 2를 반환합니다.

```bash
kubefit check \
  --first benchmarks/results/benchmark-<before-first-digest> \
  --second benchmarks/results/benchmark-<candidate-first-digest>
```

`kubefit inspect-change`는 PR의 base/candidate YAML에서 Deployment의 image,
replicas, CPU·메모리 resources 변경을 추출합니다. 다른 필드가 섞이면 안전 범위 밖으로
판정하며, 환경변수 같은 값이 CI 로그에 노출되지 않도록 미지원 변경은 경로만 출력합니다.

```bash
kubefit inspect-change \
  --base /tmp/base.yaml \
  --candidate deploy/api.yaml \
  --namespace demo \
  --deployment api
```

resource 추천에는 `kubefit validate`를 사용해 immutable proposal, 정확한 base/candidate
파일, 반대 순서 benchmark 두 개를 한 번에 결합할 수 있습니다. 파일이 proposal의
입력·출력과 byte 단위로 같지 않거나 Pair가 해당 proposal을 참조하지 않으면 INVALID로
차단합니다. 임의의 image·replica 변경을 위한 범용 benchmark bundle은 아직 없습니다.

```bash
kubefit validate \
  --proposal .kubefit/proposals/proposal-<digest> \
  --base /tmp/base.yaml \
  --candidate deploy/api.yaml \
  --first benchmarks/results/benchmark-<before-first-digest> \
  --second benchmarks/results/benchmark-<candidate-first-digest>
```

이 `action.yml`이 포함된 새 릴리스 태그를 만든 뒤에는 Linux GitHub Actions에서 Python
환경을 별도로 설치하지 않고 같은 게이트를 실행할 수 있습니다. 호출 job은 proposal과
두 result 디렉터리를 먼저 workspace에 준비해야 합니다.

```yaml
- name: Validate Kubernetes resource change
  uses: sangmu1126/kubefit@<release-tag>
  with:
    proposal: .kubefit/proposals/proposal-<digest>
    base: evidence/base.yaml
    candidate: deploy/api.yaml
    first-result: evidence/benchmark-<before-first-digest>
    second-result: evidence/benchmark-<candidate-first-digest>
```

Docker Action은 입력을 shell 문자열로 실행하지 않고 `kubefit validate`의 개별 인자로
전달합니다. 로컬 Docker build와 컨테이너 실행은 검증했지만, 아직 새 태그를 게시하지
않았으므로 원격 GitHub Actions 실행까지 완료했다고 주장하지 않습니다.

image·replica 실행 검증의 첫 경계로 `kubefit prepare-change`도 제공합니다.
정확한 base/candidate YAML과 재생된 의미적 diff를 immutable `change-<digest>` bundle로
저장하며, 같은 입력은 같은 artifact를 재사용합니다. `kubefit execute-change`는 명시적으로
확인한 disposable kind 클러스터에서 이 정확한 YAML의 배포·readiness를 검사하고, 후보가
실패하거나 실행이 중단돼도 base 복원을 시도합니다. 이는 benchmark 또는 PodKill 증거가
아닙니다.

```bash
kubefit prepare-change \
  --base /tmp/base.yaml \
  --candidate deploy/api.yaml \
  --namespace demo \
  --deployment api

kubefit execute-change \
  --change .kubefit/changes/change-<digest> \
  --context kind-kubefit \
  --container api \
  --confirm-disposable-cluster
```

공유 고정 k6 프로파일은 상호 배타적인 `change_id` 식별자를 받습니다.
`kubefit benchmark-change`는 base/candidate 부하 수집과 의무적인 base 복원을 연결하고
content-addressed `change-performance-<digest>` 결과를 발행합니다. 재생 가능한 판정은
고정 부하 완결성, latency, error rate, 복구시간으로 한정하며 비용·throttling·OOM·장애
주입을 검증했다고 주장하지 않습니다.

```bash
kubefit benchmark-change \
  --change .kubefit/changes/change-<digest> \
  --target-url http://127.0.0.1:8080 \
  --context kind-kubefit \
  --container api \
  --confirm-disposable-cluster \
  --execution-order before-after
```

두 번의 전체 부하 구간만 약 6분이 걸리며 rollout 시간은 별도입니다. FAIL과 INVALID도
artifact를 먼저 보존한 후 종료 코드 2를 반환합니다. 새 generic 경로는 로컬 단위 테스트로
검증했지만 실제 클러스터 실행까지 완료했다고 주장하지 않습니다.

두 번째 독립 실행은 `--execution-order after-before`로 수집합니다. 각 artifact는 측정
시각으로 실행 순서를 다시 검증하며, 이후 Pair 판정이 두 artifact를 결합하기 전까지는
각각 단일 순차 실행의 시간 편향 경고를 유지합니다.

## 검증된 결과

KubeFit은 비용 절감 예상치, 단일 Pair 결과, 반복 campaign을 서로 다른 증거로
관리합니다. 하나의 성공 결과처럼 합쳐 표현하지 않습니다.

| 주장 | 재현 가능한 근거 |
|---|---|
| 추천·artifact·데모 계약이 안전 조건으로 보호됨 | 현재 소스 기준 Python 테스트 466개 |
| Dashboard가 명세대로 동작하고 빌드됨 | 테스트 19개와 Vite production build |
| Helm 패키지가 최소 권한 기본값으로 렌더링됨 | Helm lint 및 기본 template 검증 |
| 공개 이미지가 실제로 기동함 | non-root `10001:10001`, health, Dashboard, 저장 비활성 smoke test |
| 통제된 Kubernetes 관측이 완료됨 | 요청 100,501건, 오류 0건, usage/throttling coverage 100% ([기록 0060](docs/devlog/0060-validation-informed-cpu-floor.md)) |
| 공격적인 절감안이 성능보다 우선하지 않음 | CPU 10m 후보와 별도 20m 반복 블록을 steady-P99 기준으로 기각 |
| 하나의 반대 순서 Pair가 전체 재생을 통과함 | [Pair 및 재분석 근거](docs/devlog/0060-validation-informed-cpu-floor.md) |
| 검증된 Pair가 검토 가능한 Git 변경으로 연결됨 | 멱등적인 [Draft PR #23](https://github.com/sangmu1126/kubefit/pull/23)과 [기록 0061](docs/devlog/0061-live-pair-draft-publication.md) |
| 반복 실험을 과장하지 않음 | 사전 등록 campaign은 `INCOMPLETE`, 평균 효과·통계적 유의성을 주장하지 않음 |
| 저장소 인증 없이 공개 패키지를 설치할 수 있음 | 익명 pull을 검증한 [v0.3.2 릴리스](https://github.com/sangmu1126/kubefit/releases/tag/v0.3.2) |

검증된 리소스 제안은 CPU `1000m/2000m → 20m/40m`, 메모리
`2Gi/4Gi → 32Mi/48Mi`입니다. 예제 단가 기준 월 request 비용 예상치는
`73.000000 → 1.396125 USD`지만, 이는 실제 AWS 청구서 절감액이 아닙니다.
노드 단편화, 할인, 세금, 오토스케일링 replica-hours는 이 계산에 포함되지 않습니다.

또한 20m 후보의 한 Pair는 7/7 정책 검사를 통과했지만, 다른 반복 블록은 한 실행
순서에서 steady P99가 21.3% 악화되어 실패했습니다. KubeFit은 이 실패와 불완전한
campaign을 그대로 보존합니다.

## 1분 데모

Docker가 실행 중이면 다음 명령 하나로 공개 이미지와 자체 검증 가능한 Pair 증거를
내려받아 실행할 수 있습니다.

```bash
./deploy/local/run-verified-pair-demo.sh
```

출력된 `http://127.0.0.1:8000/?showcase=decision-journey` 주소를 열고 다음 순서로
진행합니다.

1. **추천 계산 실행**
2. 10m 후보의 `REJECTED`와 steady-P99 회귀 확인
3. **20m Pair 검증 계속**
4. 서로 반대인 두 실행 순서와 정책 검사 7/7 확인
5. `PASS` 후 YAML diff와 기록된 Draft PR 근거 확인

이 스크립트는 loopback에만 바인딩하고 Kubernetes에 접속하지 않으며, `Ctrl+C` 시
임시 컨테이너를 제거합니다. 8000 포트를 사용 중이라면 `KUBEFIT_DEMO_PORT`를
지정할 수 있습니다.

### 시연 화면 뒤의 실제 실행 확인

시연은 터미널 창 하나와 탭 두 개면 충분합니다. [로컬 Kubernetes
환경](docs/local-development.md)이 이미 실행 중이면 첫 번째 탭에서 kind,
Prometheus, 데모 Deployment가 실제로 동작하는지 확인할 수 있습니다.

```bash
docker ps
kubectl --context kind-kubefit get pods -n monitoring
kubectl --context kind-kubefit get pods -n kubefit-demo
kubectl --context kind-kubefit get deployment overprovisioned-api \
  -n kubefit-demo \
  -o jsonpath='{.spec.template.spec.containers[0].resources}'
```

두 번째 탭에서 다음 명령을 잠시 실행하고 `http://127.0.0.1:9090`을 열면 실제
Prometheus 수집 상태를 확인할 수 있습니다.

```bash
kubectl --context kind-kubefit port-forward \
  -n monitoring \
  svc/monitoring-kube-prometheus-prometheus \
  9090:9090
```

기본 저부하 확인은 **Status → Target health**에서 `kubelet`으로 필터링한 뒤
`/metrics`, `/metrics/cadvisor`, `/metrics/probes`가 `UP`인지 보는 방식입니다.
KubeFit이 사용하는 컨테이너 CPU·메모리의 원천은 cAdvisor endpoint입니다. Target
health는 수집 연결을 증명할 뿐 추천의 안전성이나 benchmark 성공을 증명하지 않습니다.

선택적으로 다음 PromQL을 실행하면 현재 Pod별 CPU 사용량을 millicore 단위로 볼 수
있습니다.

```promql
sum by (pod) (
  rate(container_cpu_usage_seconds_total{
    namespace="kubefit-demo",
    container="api"
  }[5m])
) * 1000
```

데모 Deployment에 요청이 없다면 `0m`이 정상이며, 검증 Pair 시연을 위해 별도
트래픽을 만들 필요가 없습니다. Collector는 하나의 현재값이 아니라 coverage 조건이
있는 관측 구간 전체에서 이 5분 rate를 사용합니다.

검증 Pair 데모를 시작한 뒤 다른 탭에서 다음 명령을 실행하면 FastAPI 상태와 화면의
`PASS`가 프론트엔드 상수가 아니라 서버의 전체 artifact 재검증 결과임을 확인할 수
있습니다.

```bash
curl --fail http://127.0.0.1:8000/healthz

curl --silent \
  http://127.0.0.1:8000/v1/benchmark-pairs/benchmark-pair-dbc41864dd0dba9537ef228ebb340f60/review \
  | jq '{status, verification_level, benchmark_ids, checks: [.checks[].status]}'
```

`jq`는 화면에 보여줄 출력을 줄이는 용도일 뿐이며, 없어도 Pair API의 전체 검증은
동일하게 실행됩니다. Prometheus 화면은 실시간 수집 경로를 보여주고, Showcase는
별도로 보존해 digest로 고정한 Pair를 재생합니다. 현재 Prometheus 표본이 기록된
과거 verdict를 만들었다고 주장하지 않습니다.

공개 v0.3.2 대신 커밋하지 않은 현재 소스를 확인하려면 다음처럼 실행합니다.

```bash
KUBEFIT_DEMO_BUILD_LOCAL=true ./deploy/local/run-verified-pair-demo.sh
```

수정 가능한 예제 평가 화면만 열려면 다음 명령을 사용합니다. 이 화면은 예제 입력을
API로 평가하며 클러스터에서 메트릭을 수집하거나 클러스터를 변경하지 않습니다.

```bash
docker run --rm -p 127.0.0.1:8000:8000 \
  ghcr.io/sangmu1126/kubefit:0.3.2
```

브라우저에서 `http://127.0.0.1:8000`을 엽니다.

## 저장소 구조

```text
collector/       Kubernetes·Prometheus 어댑터
recommender/     리소스 추천 도메인 로직
evaluator/       비용·안정성·성능 평가
gitops/          YAML 패치·GitHub Draft PR 연동
api/             FastAPI 애플리케이션과 CLI
dashboard/       React 추천 검토 Dashboard
deploy/          Helm chart와 로컬 데모 환경
benchmarks/      k6 부하 테스트와 재현 가능한 비교
safety/          변경 분석, immutable change bundle, CI 증거 결합
docs/            아키텍처·보안·평가·개발기록
tests/           단위·통합·계약 테스트
```

상세 설계와 근거는 다음 문서에서 확인할 수 있습니다.

- [구현 순서와 완료 기준](docs/implementation-plan.md)
- [아키텍처](docs/architecture.md)
- [로컬 개발·kind·Prometheus 실행](docs/local-development.md)
- [개발기록 86개](docs/devlog/README.md)
- [GitHub 실증 절차](docs/live-github-demo.md)
- [기여 안내](CONTRIBUTING.md)
- [보안 정책](SECURITY.md)

## 개발 환경 설치

Python 3.12 이상이 필요합니다.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements/build.lock
python -m pip install --require-hashes -r requirements/dev.lock
python -m pip install --no-deps --no-build-isolation -e .
python -m pip check
pytest -q
uvicorn api.main:app --reload
```

실행 후 `http://localhost:8000/docs`에서 API 문서를 확인합니다.
`POST /v1/recommendations`는 추천 결과를, `POST /v1/evaluations`는 추천·위험·비용
비교를 함께 반환합니다.

Dashboard 개발 명령은 다음과 같습니다.

```bash
cd dashboard
npm ci
npm test -- --run
npm run build
```

## 실제 Deployment 분석

Prometheus를 로컬에서 접근할 수 있는 상태에서 먼저 관측 준비도를 확인합니다.

```bash
kubefit readiness --context kind-kubefit \
  --namespace kubefit-demo --deployment overprovisioned-api \
  --prometheus-url http://localhost:9090 \
  --identity-store .kubefit/identities.json --days 1
```

준비도가 충족되면 비용 단가와 출처를 명시해 분석합니다.

```bash
kubefit analyze --namespace kubefit-demo --deployment overprovisioned-api \
  --prometheus-url http://localhost:9090 \
  --identity-store .kubefit/identities.json \
  --cpu-core-hour-usd 0.04 \
  --memory-gib-hour-usd 0.005 \
  --price-source example://local-model
```

CLI는 현재 `kubectl` context에서 Deployment와 Pod 메타데이터를 읽고 Prometheus를
조회합니다. Prometheus에는 cAdvisor 지표와 kube-state-metrics의
`kube_pod_owner`가 필요합니다. KubeFit은 Deployment UID·생성 시각과 controller
owner UID로 관측 대상을 고정하여 이름이 같은 과거 워크로드가 섞이지 않게 합니다.

운영 기본 관측은 대표성 있는 다일 트래픽을 전제로 합니다. 대회용 통제 데모에서는
하루 동안 유휴 트래픽을 기다리지 말고, 고정 1시간 k6 프로필과
`--observation-profile demo`를 readiness와 analyze에 동일하게 사용합니다. 데모
프로필 결과는 운영 트래픽을 대표한다고 주장할 수 없습니다.

자세한 kind·Prometheus 설치 및 분석 절차는
[로컬 개발 가이드](docs/local-development.md)를 참고하세요.

## 추천과 안전 판단

- CPU request는 관측 CPU P95에 안전 여유를 적용합니다.
- 메모리 request는 관측 memory P99에 안전 여유를 적용합니다.
- limit는 request와 최대 관측값을 함께 고려합니다.
- 관측 coverage, 샘플 수, replica 안정성, Pod별 신호가 부족하면 변경을 차단합니다.
- throttling 지표나 Pod 상태가 불완전하면 위험을 임의로 `low`로 만들지 않고
  `unknown`으로 표시합니다.
- OOMKilled가 관측되면 전체 관측 창이 불완전해도 높은 위험으로 표시합니다.
- 비용 절감 수치는 latency·error·OOM·throttling 정책을 통과시키지 못합니다.

모든 평가는 `patch_eligibility`를 포함합니다. GitOps 패치는 `eligible`일 때만
생성할 수 있으며, 중간 위험은 자동 통과가 아니라 reviewer 경고로 남습니다.

## YAML과 GitOps 경계

KubeFit은 대상 `apps/v1 Deployment`와 컨테이너를 정확히 한 개로 식별하고, 분석
당시 리소스 값과 저장소 YAML이 여전히 같은지 확인합니다. 조건이 맞을 때만 다음
네 scalar를 변경합니다.

- CPU request
- CPU limit
- memory request
- memory limit

그 외 문서, 필드, 주석, 순서와 quoting은 보존합니다. 오래되었거나 모호한 YAML,
symlink, dirty Git 상태, 예상과 다른 remote ref는 fail-closed 방식으로 거부합니다.

`kubefit publish`는 명시적인 `--confirm-publish`와 환경변수의 GitHub token을
요구합니다. 전용 브랜치와 Draft PR만 생성하며 병합하거나 배포하지 않습니다.
동일한 제안은 재사용하고, 같은 이름의 다른 내용은 덮어쓰지 않습니다.

## Benchmark 증거 경계

단일 before/after 실행은 시간 순서 편향을 가질 수 있습니다. 따라서 필수 publication
gate는 같은 제안을 다음 두 순서로 실행한 Pair를 사용합니다.

```text
before → after
after  → before
```

두 결과는 평균으로 숨기지 않고 각각의 변화와 관측 범위를 표시합니다. Pair가
PASS하려면 두 순서의 정책 검사가 모두 통과해야 합니다. 반복 campaign은 선택적인
고급 증거이며 현재 분산, 신뢰구간, 통계적 유의성을 계산하지 않습니다.

Benchmark 실행은 명시적으로 확인한 disposable `kind-*` 클러스터로 제한되고, 변경이
시작된 모든 종료 경로에서 원래 manifest 복원을 시도합니다. 이는 운영 클러스터 자동
최적화 인터페이스가 아닙니다.

## SBOM과 공급망 범위

공개 이미지는 BuildKit SBOM·provenance attestation과 함께 빌드됩니다. 로컬에서는
정확한 이미지 ID에 바인딩된 SPDX 2.3 inventory를 생성하고 재검증할 수 있습니다.

```bash
docker pull ghcr.io/sangmu1126/kubefit:0.3.2
KUBEFIT_IMAGE_REFERENCE=ghcr.io/sangmu1126/kubefit:0.3.2 \
  ./deploy/local/generate-image-sbom.sh
```

이 SBOM은 패키지 inventory 증거이지 취약점 스캔, maintainer 서명, 라이선스 준수
인증이 아닙니다. Dashboard는 정적 asset으로 번들되므로 JavaScript 의존성의 기준은
`dashboard/package-lock.json`이며, 최종 이미지 scanner가 이를 개별 패키지로 모두
표시하지 않을 수 있습니다.

## 프로젝트의 출발점

KubeFit은 기존 서버리스 플랫폼을 운영하며 경험한 리소스 과다 할당과 관측성 문제에서
출발했습니다. 그러나 기존 FaaS 코드베이스를 확장한 것이 아니라, 독립적인 오픈소스
Kubernetes 최적화 도구로 새롭게 설계·구현했습니다. 이전 프로젝트는 문제 발견의
배경이며 이 저장소의 배포 구조나 코드베이스가 아닙니다.

## 기여와 보안

기여를 환영합니다. 개발 환경, 품질 gate, 설계 경계와 PR 절차는
[CONTRIBUTING.md](CONTRIBUTING.md)를 확인하세요. 버그와 기능 제안에는 GitHub의
구조화된 issue form을 사용하고 모든 프로젝트 공간에서
[행동강령](CODE_OF_CONDUCT.md)을 준수해 주세요.

취약점 의심 내용을 공개 issue에 작성하지 마세요. 지원 버전과 비공개 신고 경로는
[SECURITY.md](SECURITY.md)에 정리되어 있습니다.

## 안전 원칙

- 추천 경로에서는 워크로드와 메트릭을 읽기만 하고 클러스터를 변경하지 않습니다.
- 변경안은 근거와 rollback 지침이 포함된 Draft PR로 제출합니다.
- Kubernetes, Prometheus, GitHub 자격 증명을 저장소에 저장하지 않습니다.
- 추천 정책은 결정적이며 독립적으로 테스트할 수 있어야 합니다.
- 실패한 benchmark와 불완전한 증거를 성공 결과로 바꾸거나 숨기지 않습니다.

## 라이선스

KubeFit은 [Apache License 2.0](LICENSE)으로 배포됩니다.
