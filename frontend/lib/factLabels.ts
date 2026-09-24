// Human names for the "_"-prefixed sentinel facts extraction writes for
// things that are not ordinary field values. Shared by the Workbench queue
// and the document review view so both describe an item the same way.
export function sentinelLabel(fieldName: string, t: (key: string, fallback: string) => string): string {
  if (fieldName === "_marginalia") return t("workbench.sentinel.marginalia", "Handwritten margin note");
  if (fieldName === "_join_mismatch") return t("workbench.sentinel.join_mismatch", "Table join couldn't be matched");
  if (fieldName === "_stitch_ambiguous") return t("workbench.sentinel.stitch_ambiguous", "Table continuation unclear");
  return fieldName;
}
