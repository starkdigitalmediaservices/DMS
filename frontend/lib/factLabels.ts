// Human names for the "_"-prefixed sentinel facts extraction writes for
// things that are not ordinary field values. Shared by the Workbench queue
// and the document review view so both describe an item the same way.
import { humanFieldName } from "./fieldLabels";

export function sentinelLabel(fieldName: string, t: (key: string, fallback: string) => string): string {
  if (fieldName === "_marginalia") return t("check.kind.margin_note", "Note written in the margin");
  if (fieldName === "_join_mismatch") return t("check.kind.join_mismatch", "Left and right halves of a row didn't line up");
  if (fieldName === "_stitch_ambiguous") return t("check.kind.stitch", "Does the table continue on the next page?");
  return humanFieldName(fieldName);
}
