#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 6 ]]; then
  echo "usage: $0 IMAGE PROPOSAL BASE CANDIDATE FIRST_RESULT SECOND_RESULT" >&2
  exit 1
fi

image_reference="$1"
proposal_path="$2"
base_path="$3"
candidate_path="$4"
first_result_path="$5"
second_result_path="$6"
repository_root="$(git rev-parse --show-toplevel)"
temporary_root="$(mktemp -d "${TMPDIR:-/tmp}/kubefit-action-smoke.XXXXXX")"

cleanup() {
  exit_status=$?
  rm -rf "${temporary_root}"
  exit "${exit_status}"
}
trap cleanup EXIT

for relative_path in \
  "${proposal_path}" \
  "${base_path}" \
  "${candidate_path}" \
  "${first_result_path}" \
  "${second_result_path}"; do
  if [[ -z "${relative_path}" || "${relative_path}" == /* || "${relative_path}" == *..* ]]; then
    echo "evidence paths must be safe repository-relative paths: ${relative_path}" >&2
    exit 1
  fi
  if [[ ! -e "${repository_root}/${relative_path}" || -L "${repository_root}/${relative_path}" ]]; then
    echo "evidence path is missing or symlinked: ${relative_path}" >&2
    exit 1
  fi
done

docker image inspect "${image_reference}" >/dev/null
cp "${repository_root}/${candidate_path}" "${temporary_root}/tampered-candidate.yaml"
printf '\n' >> "${temporary_root}/tampered-candidate.yaml"

container_arguments=(
  --rm
  --workdir /github/workspace
  --volume "${repository_root}:/github/workspace:ro"
  --volume "${temporary_root}:/github/kubefit-smoke"
)
validation_arguments=(
  validate
  --proposal "/github/workspace/${proposal_path}"
  --base "/github/workspace/${base_path}"
  --first "/github/workspace/${first_result_path}"
  --second "/github/workspace/${second_result_path}"
)

docker run \
  "${container_arguments[@]}" \
  --env GITHUB_STEP_SUMMARY=/github/kubefit-smoke/pass-summary.md \
  "${image_reference}" \
  "${validation_arguments[@]}" \
  --candidate "/github/workspace/${candidate_path}" \
  > "${temporary_root}/pass.json"
grep -q '^## ✅ PASS$' "${temporary_root}/pass-summary.md"

set +e
docker run \
  "${container_arguments[@]}" \
  --env GITHUB_STEP_SUMMARY=/github/kubefit-smoke/invalid-summary.md \
  "${image_reference}" \
  "${validation_arguments[@]}" \
  --candidate /github/kubefit-smoke/tampered-candidate.yaml \
  > "${temporary_root}/invalid.json"
invalid_exit=$?
set -e

if [[ "${invalid_exit}" -ne 2 ]]; then
  echo "tampered candidate must return exit code 2, got ${invalid_exit}" >&2
  exit 1
fi
grep -q '^## ⚠️ INVALID$' "${temporary_root}/invalid-summary.md"

echo "KubeFit Action passed valid evidence and rejected a byte-mismatched candidate."
