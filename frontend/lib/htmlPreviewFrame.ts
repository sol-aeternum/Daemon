export const HTML_PREVIEW_FRAME_PATH = '/api/previews/html';
export const HTML_PREVIEW_MESSAGE_TYPE = 'daemon:html-preview';

export interface HtmlPreviewMessage {
  type: typeof HTML_PREVIEW_MESSAGE_TYPE;
  content: string;
  title: string;
}
