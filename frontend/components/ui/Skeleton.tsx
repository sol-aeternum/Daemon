interface SkeletonLineProps {
  width?: string | number;
  height?: string | number;
  className?: string;
}

interface SkeletonBlockProps {
  width?: string | number;
  height?: string | number;
  className?: string;
}

interface SkeletonCircleProps {
  size?: string | number;
  className?: string;
}

/**
 * SkeletonLine - A single horizontal line skeleton with shimmer animation
 */
export function SkeletonLine({
  width = '100%',
  height = '1rem',
  className = '',
}: SkeletonLineProps) {
  const widthStyle = typeof width === 'number' ? `${width}px` : width;
  const heightStyle = typeof height === 'number' ? `${height}px` : height;

  return (
    <div
      className={`skeleton rounded ${className}`}
      style={{
        width: widthStyle,
        height: heightStyle,
      }}
    />
  );
}

/**
 * SkeletonBlock - A rectangular skeleton block with shimmer animation
 */
export function SkeletonBlock({
  width = '100%',
  height = '6rem',
  className = '',
}: SkeletonBlockProps) {
  const widthStyle = typeof width === 'number' ? `${width}px` : width;
  const heightStyle = typeof height === 'number' ? `${height}px` : height;

  return (
    <div
      className={`skeleton rounded-lg ${className}`}
      style={{
        width: widthStyle,
        height: heightStyle,
      }}
    />
  );
}

/**
 * SkeletonCircle - A circular skeleton with shimmer animation
 */
export function SkeletonCircle({
  size = '3rem',
  className = '',
}: SkeletonCircleProps) {
  const sizeStyle = typeof size === 'number' ? `${size}px` : size;

  return (
    <div
      className={`skeleton rounded-full ${className}`}
      style={{
        width: sizeStyle,
        height: sizeStyle,
      }}
    />
  );
}
