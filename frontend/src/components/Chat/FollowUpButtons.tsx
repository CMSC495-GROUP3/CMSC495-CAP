/**
 * FollowUpButtons — 3 clickable suggested follow-up questions.
 * Only rendered on the last assistant message. Clicking one fires sendMessage immediately.
 */
interface Props {
  questions: string[]
  onSelect: (q: string) => void
}

export default function FollowUpButtons({ questions, onSelect }: Props) {
  if (!questions.length) return null

  return (
    <div className="mt-3 flex flex-col gap-1.5">
      {questions.map((q) => (
        <button
          key={q}
          onClick={() => onSelect(q)}
          className="text-left text-xs px-3 py-2 rounded-lg border border-[#C2B067]/15 bg-[#C2B067]/5 text-gray-300 hover:bg-[#C2B067]/12 hover:text-gray-100 hover:border-[#C2B067]/30 transition-colors cursor-pointer"
        >
          {q}
        </button>
      ))}
    </div>
  )
}
