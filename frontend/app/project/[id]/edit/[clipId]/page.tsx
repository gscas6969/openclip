"use client";

import { useParams } from "next/navigation";
import { EditorWorkspace } from "@/components/editor/EditorWorkspace";

export default function ClipEditorPage() {
    const params = useParams();
    const projectId = params.id as string;
    const clipId = params.clipId as string;

    return <EditorWorkspace projectId={projectId} clipId={clipId} />;
}
