document.getElementById('year').textContent = new Date().getFullYear();
const reveals = [...document.querySelectorAll('.reveal')];
reveals.forEach((el, index) => { el.style.setProperty('--reveal-delay', `${Math.min(index % 3, 2) * 70}ms`); });
const observer = new IntersectionObserver((entries) => {
  for (const entry of entries) if (entry.isIntersecting) { entry.target.classList.add('is-visible'); observer.unobserve(entry.target); }
}, { threshold: 0.12 });
reveals.forEach((el) => observer.observe(el));
