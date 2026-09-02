const MAX_FILE_BYTES = 100 * 1024 * 1024;
const MAX_BATCH_BYTES = 100 * 1024 * 1024;

export function uploadSizeError(files: readonly { name: string; size: number }[]): string | null {
  const oversized = files.find((file) => file.size > MAX_FILE_BYTES);
  if (oversized) return `${oversized.name}: 파일은 100MB를 넘을 수 없습니다.`;
  if (files.reduce((total, file) => total + file.size, 0) > MAX_BATCH_BYTES) {
    return "한 번에 업로드할 수 있는 파일의 합계는 100MB입니다. 파일을 나누어 신청해 주세요.";
  }
  return null;
}
