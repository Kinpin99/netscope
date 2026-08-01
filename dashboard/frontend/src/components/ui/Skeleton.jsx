const Skeleton = ({ width, height, className = '' }) => {
  return (
    <div
      className={`skeleton rounded ${className}`}
      style={{
        width: typeof width === 'number' ? `${width}px` : width,
        height: typeof height === 'number' ? `${height}px` : height,
      }}
    />
  );
};

export default Skeleton;
