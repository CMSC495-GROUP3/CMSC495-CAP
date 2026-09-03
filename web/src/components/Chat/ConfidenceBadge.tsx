/**
 * ConfidenceBadge — retrieval-similarity indicator shown beneath an answer.
 *
 * This is the mean similarity of the passages the answer was drawn from, not a
 * probability that the answer is correct. The label says "retrieval match" for
 * exactly that reason: calling it "confidence" invites readers to treat it as a
 * correctness score, which it is not.
 */
const STRONG_MATCH = 70
const PARTIAL_MATCH = 40

interface Props {
  confidence: number | null | undefined
}

export default function ConfidenceBadge({ confidence }: Props) {
  if (confidence == null) return null

  const color =
    confidence >= STRONG_MATCH
      ? 'bg-green-500'
      : confidence >= PARTIAL_MATCH
      ? 'bg-orange-400'
      : 'bg-red-500'

  return (
    <span
      className="inline-flex items-center gap-1.5 text-xs text-gray-400"
      title="Average similarity between your question and the retrieved passages. Not a measure of factual accuracy."
    >
      <span className={`w-2 h-2 rounded-full ${color}`} />
      {confidence}% retrieval match
    </span>
  )
}
