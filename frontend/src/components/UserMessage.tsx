import React from "react";
import { AttachmentChip } from "./AttachmentChip";

export function UserMessage({
	content,
	attachments,
}: {
	content: string;
	attachments: Array<{ file_name: string; file_size?: number }>;
}) {
	return (
		<div className="faip-user-wrap">
			<div className="faip-user-message">
				{attachments.length ? (
					<div className="faip-user-attachments">
						{attachments.map((attachment, index) => (
							<AttachmentChip
								key={`${attachment.file_name}-${index}`}
								fileName={attachment.file_name}
								fileSize={attachment.file_size}
							/>
						))}
					</div>
				) : null}
				<div className="faip-text-block">{content}</div>
			</div>
		</div>
	);
}
