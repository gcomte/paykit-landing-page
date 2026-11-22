// Shared with the real client player; the export does not load remote media.
export const teaserVideo = {
  src: "https://www.youtube-nocookie.com/embed/cQi8QkG_y4Y?autoplay=1&rel=0",
  title: "Paykit teaser",
  playLabel: "Play the Paykit teaser. Loads video from YouTube.",
};

export function inquiryDraftStatus(contactEmail: string) {
  return `Send the draft in your email app. If it did not open, email ${contactEmail} directly.`;
}
